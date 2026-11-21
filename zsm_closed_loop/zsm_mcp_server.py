import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("O-RAN-ZSM-ClosedLoop")
PMS_URL = "http://localhost:8081/a1-policy/v2"
KPI_URL = "http://localhost:8090/kpis"


@mcp.tool()
def get_cell_kpis() -> dict:
    """Coleta KPIs em tempo real (PRB, latencia, perda de pacotes e status de SLA) das celulas da RAN."""
    resp = requests.get(KPI_URL, timeout=5)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_near_rt_rics() -> dict:
    """Lista todos os Near-RT RICs registrados no Non-RT RIC, seus estados e policy types suportados."""
    resp = requests.get(f"{PMS_URL}/rics", timeout=5)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_policy_type_schema(policytype_id: str) -> dict:
    """Retorna o JSON Schema obrigatorio para criar politicas de um determinado policytype_id."""
    resp = requests.get(f"{PMS_URL}/policy-types/{policytype_id}", timeout=5)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def list_active_policies() -> dict:
    """Lista os IDs de todas as politicas A1 atualmente ativas no Non-RT RIC."""
    resp = requests.get(f"{PMS_URL}/policies", timeout=5)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def apply_a1_policy(
    ric_id: str,
    policy_id: str,
    policytype_id: str,
    policy_data: dict,
) -> dict:
    """Cria ou atualiza uma politica A1 no Non-RT RIC para remediacao de SLA."""
    payload = {
        "ric_id": ric_id,
        "policy_id": policy_id,
        "policytype_id": policytype_id,
        "service_id": "zsm-agentic-rapp",
        "policy_data": policy_data,
    }
    resp = requests.put(f"{PMS_URL}/policies", json=payload, timeout=5)
    if not resp.ok:
        return {"error": True, "status_code": resp.status_code, "detail": resp.text}
    return {"error": False, "status_code": resp.status_code, "applied": payload}


if __name__ == "__main__":
    mcp.run()
