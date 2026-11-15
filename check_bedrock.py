"""
check_bedrock.py : run from the tradedesk folder:  python check_bedrock.py
Makes one tiny call to each model and one read-only DynamoDB check.
Changes nothing in AWS. Costs a fraction of a cent.
"""
import boto3
from dotenv import load_dotenv
load_dotenv()
import config
from src.client.model_client import ModelClient

print(f"Model region : {config.BEDROCK_MODEL_REGION}")
print(f"Table region : {config.BEDROCK_REGION}")
print(f"Bedrock API key set: {config.BEDROCK_API_KEY_SET} | IAM keys set: {config.IAM_KEYS_SET}\n")

client = ModelClient()
for label, thinking in (("fast", False), ("reasoning", True)):
    model_id = client._resolve_model(label)
    try:
        r = client.create_message(
            model=label, use_thinking=thinking,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
        )
        text = "".join(b.text for b in r.content if hasattr(b, "text")).strip()
        print(f"PASS  {model_id}  ->  {text[:40]!r}")
    except Exception as e:
        print(f"FAIL  {model_id}\n      {type(e).__name__}: {e}")

try:
    boto3.client("dynamodb", region_name=config.BEDROCK_REGION).describe_table(
        TableName=config.DYNAMODB_TABLE_NAME)
    print(f"PASS  DynamoDB table {config.DYNAMODB_TABLE_NAME!r} exists")
except Exception as e:
    print(f"FAIL  DynamoDB table {config.DYNAMODB_TABLE_NAME!r}\n      {type(e).__name__}: {e}")
