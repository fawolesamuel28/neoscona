from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

# 1. Message Counters
MESSAGES_TOTAL = Counter(
    "reva_messages_total", 
    "Total messages processed", 
    ["source", "stage"]
)

# 2. LLM Latency
LLM_LATENCY = Histogram(
    "reva_llm_duration_seconds", 
    "LLM response latency in seconds", 
    ["provider"],
    buckets=(1.0, 2.0, 5.0, 10.0, 30.0, 60.0, float("inf"))
)

# 3. Queue Depth
QUEUE_DEPTH = Gauge(
    "reva_queue_depth", 
    "Number of tasks waiting in queue"
)

# 4. Conversion Progress
LEAD_CONVERSION = Gauge(
    "reva_lead_stage_count", 
    "Count of leads per stage", 
    ["stage"]
)

# 5. Tool Call Metrics
TOOL_CALLS = Counter(
    "reva_tool_calls_total",
    "Total tool calls by Reva",
    ["tool", "status"]
)

# 6. Plan Entitlement Blocks (Phase 2b enforcement)
ENTITLEMENT_BLOCKS = Counter(
    "reva_entitlement_blocks_total",
    "Requests/replies blocked by plan entitlement enforcement",
    ["reason"]
)

def metrics_endpoint():
    """FastAPI endpoint to export metrics."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
