"""
create_knowledge_bases.py
==========================
Provision the three NovaMart Bedrock Knowledge Bases **by code** (project Task 5),
instead of clicking through the AWS Console.

Each KB is self-managed over the S3 Vectors store the CloudFormation stack already
created (one vector bucket, three indexes), with Titan Embed Text v2 embeddings and
an S3 data source pointed at one policy prefix:

    novamart-returns-policy-kb   policies/returns/    returns-policy-index
    novamart-shipping-policy-kb  policies/shipping/   shipping-policy-index
    novamart-warranty-policy-kb  policies/warranty/   warranty-policy-index

The KB service role is the stack's AgentCore execution role
(`config.AGENTCORE_ROLE_ARN`): its trust policy already allows `bedrock.amazonaws.com`
to assume it, and it already grants `bedrock:InvokeModel` (the embedder),
`s3:GetObject`/`s3:ListBucket` (the data bucket) and `s3vectors:*` (the vector store).
No separate role is created.

Everything is idempotent (describe-or-create by name). On success the three KB IDs are
written back into `.env`. Teardown is handled by `infrastructure/cleanup.py` (it finds
KBs by the `novamart-` name prefix).

Usage (from the project root, project credentials/region in .env):

    python infrastructure/create_knowledge_bases.py            # create/sync, write .env
    python infrastructure/create_knowledge_bases.py --status   # report only, no changes

If the S3 Vectors KB API or sandbox permissions do not support this cleanly, fall back
to the AWS Console runbook in docs/HOW_TO_RUN.md and record the decision in
docs/BUILD_LOG.md.
"""

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

REGION = config.AWS_REGION
EMBED_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
INGESTION_TIMEOUT = 600          # seconds to wait for a sync to COMPLETE
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

# domain -> (kb name, S3 prefix, vector index getter, env key)
KBS = [
    ('Returns',  'novamart-returns-policy-kb',  'policies/returns/',  'RETURNS_VECTOR_INDEX',  'RETURNS_KB_ID'),
    ('Shipping', 'novamart-shipping-policy-kb', 'policies/shipping/', 'SHIPPING_VECTOR_INDEX', 'SHIPPING_KB_ID'),
    ('Warranty', 'novamart-warranty-policy-kb', 'policies/warranty/', 'WARRANTY_VECTOR_INDEX', 'WARRANTY_KB_ID'),
]

bedrock_agent = boto3.client('bedrock-agent', region_name=REGION)


def _say(msg: str) -> None:
    print(f"  {msg}", flush=True)


def _find_kb_by_name(name: str) -> str:
    """Return the KB id for a given name, or '' if it does not exist."""
    paginator = bedrock_agent.get_paginator('list_knowledge_bases')
    for page in paginator.paginate():
        for kb in page.get('knowledgeBaseSummaries', []):
            if kb['name'] == name:
                return kb['knowledgeBaseId']
    return ''


def _vector_index_arn(index_name: str) -> str:
    vector_bucket_arn = config.VECTOR_STORE_BUCKET_ARN
    return f"{vector_bucket_arn}/index/{index_name}"


def _create_or_get_kb(name: str, index_name: str) -> str:
    """Describe-or-create one self-managed S3 Vectors Knowledge Base."""
    existing = _find_kb_by_name(name)
    if existing:
        _say(f"[exists] KB {name} -> {existing}")
        return existing

    resp = bedrock_agent.create_knowledge_base(
        name=name,
        description=f"NovaMart {name} (self-managed, S3 Vectors, Titan v2).",
        roleArn=config.AGENTCORE_ROLE_ARN,
        knowledgeBaseConfiguration={
            'type': 'VECTOR',
            'vectorKnowledgeBaseConfiguration': {
                'embeddingModelArn': EMBED_MODEL_ARN,
                'embeddingModelConfiguration': {
                    'bedrockEmbeddingModelConfiguration': {'dimensions': 1024}
                },
            },
        },
        storageConfiguration={
            'type': 'S3_VECTORS',
            's3VectorsConfiguration': {
                'vectorBucketArn': config.VECTOR_STORE_BUCKET_ARN,
                'indexArn': _vector_index_arn(index_name),
            },
        },
    )
    kb_id = resp['knowledgeBase']['knowledgeBaseId']
    _say(f"[created] KB {name} -> {kb_id}")
    return kb_id


def _create_or_get_data_source(kb_id: str, name: str, prefix: str) -> str:
    """Describe-or-create the S3 data source for one KB prefix."""
    for ds in bedrock_agent.list_data_sources(knowledgeBaseId=kb_id).get('dataSourceSummaries', []):
        if ds['name'] == f"{name}-source":
            return ds['dataSourceId']

    policy_bucket_arn = f"arn:aws:s3:::{config.POLICY_BUCKET}"
    resp = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=f"{name}-source",
        dataSourceConfiguration={
            'type': 'S3',
            's3Configuration': {
                'bucketArn': policy_bucket_arn,
                'inclusionPrefixes': [prefix],
            },
        },
    )
    ds_id = resp['dataSource']['dataSourceId']
    _say(f"[created] data source {name}-source -> {ds_id} (prefix {prefix})")
    return ds_id


def _sync(kb_id: str, ds_id: str, label: str) -> None:
    """Start an ingestion job if none has completed, and wait for COMPLETE."""
    jobs = bedrock_agent.list_ingestion_jobs(
        knowledgeBaseId=kb_id, dataSourceId=ds_id).get('ingestionJobSummaries', [])
    if any(j.get('status') == 'COMPLETE' for j in jobs):
        _say(f"[synced] {label} already has a completed ingestion job")
        return

    job = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    job_id = job['ingestionJob']['ingestionJobId']
    _say(f"[sync] {label} ingestion {job_id} started; waiting for COMPLETE")

    deadline = time.time() + INGESTION_TIMEOUT
    while time.time() < deadline:
        status = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id
        )['ingestionJob']['status']
        if status == 'COMPLETE':
            _say(f"[synced] {label} ingestion COMPLETE")
            return
        if status in ('FAILED', 'STOPPED'):
            raise RuntimeError(f"{label} ingestion job {job_id} ended {status}")
        time.sleep(10)
    raise TimeoutError(f"{label} ingestion job {job_id} not COMPLETE after {INGESTION_TIMEOUT}s")


def _wait_kb_active(kb_id: str, label: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = bedrock_agent.get_knowledge_base(
            knowledgeBaseId=kb_id)['knowledgeBase']['status']
        if status == 'ACTIVE':
            return
        if status == 'FAILED':
            raise RuntimeError(f"{label} KB entered FAILED")
        time.sleep(5)
    raise TimeoutError(f"{label} KB not ACTIVE after {timeout}s")


def _write_env(ids: dict) -> None:
    """Write the three KB IDs into .env (create keys if missing), preserving the rest."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
    keys = dict(ids)
    out = []
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line else ''
        if key in keys:
            out.append(f"{key}={keys.pop(key)}")
        else:
            out.append(line)
    for key, value in keys.items():
        out.append(f"{key}={value}")
    with open(ENV_PATH, 'w', encoding='utf-8') as fh:
        fh.write("\n".join(out) + "\n")
    _say(f"[.env] wrote {', '.join(ids)}")


def status() -> None:
    for domain, name, prefix, _idx, env_key in KBS:
        kb_id = _find_kb_by_name(name)
        if not kb_id:
            _say(f"{domain}: not created")
            continue
        kb = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']
        _say(f"{domain}: {kb_id} status={kb['status']}")


def provision() -> None:
    print(f"\nProvisioning NovaMart Knowledge Bases in {REGION} "
          f"(vector bucket {config.VECTOR_STORE_BUCKET})\n")
    ids = {}
    for domain, name, prefix, idx_const, env_key in KBS:
        index_name = getattr(config, idx_const)
        kb_id = _create_or_get_kb(name, index_name)
        _wait_kb_active(kb_id, domain)
        ds_id = _create_or_get_data_source(kb_id, name, prefix)
        _sync(kb_id, ds_id, domain)
        ids[env_key] = kb_id
    _write_env(ids)
    print("\nAll three Knowledge Bases are provisioned and synced.\n")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description="Provision NovaMart Bedrock Knowledge Bases by code.")
    ap.add_argument('--status', action='store_true', help="report KB status only; make no changes")
    args = ap.parse_args()
    try:
        if args.status:
            status()
        else:
            provision()
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        print(f"\n  [ERROR] {code}: {exc.response.get('Error', {}).get('Message', str(exc))}")
        print("  If this is an unsupported-API or permissions error, use the AWS Console runbook "
              "in docs/HOW_TO_RUN.md and record it in docs/BUILD_LOG.md.")
        sys.exit(1)
