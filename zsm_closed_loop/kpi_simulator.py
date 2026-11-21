import json
import random
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests

PMS_URL = "http://localhost:8081/a1-policy/v2"
STATE = {"congestion_kista_1": False}


def get_active_priority_for_ue(target_ue: str) -> float:
    """Consulta o Non-RT RIC (PMS) para verificar se ha politica A1 ativa para o UE."""
    try:
        r = requests.get(f"{PMS_URL}/policies", timeout=2)
        if not r.ok:
            return 0.0
        max_priority = 0.0
        for pid in r.json().get("policy_ids", []):
            pr = requests.get(f"{PMS_URL}/policies/{pid}", timeout=2)
            if pr.ok:
                pdata = pr.json().get("policy_data", {})
                if pdata.get("scope", {}).get("ueId") == target_ue:
                    prio = float(pdata.get("qosObjectives", {}).get("priorityLevel", 0))
                    max_priority = max(max_priority, prio)
        return max_priority
    except Exception:
        return 0.0


def build_kpis() -> dict:
    ue1_priority = get_active_priority_for_ue("ue-001")
    congested = STATE["congestion_kista_1"]

    # Se ha congestionamento na kista_1, so mitiga quando A1 policy tiver priorityLevel >= 20
    if congested and ue1_priority < 20:
        k1_prb = round(random.uniform(90.0, 96.0), 1)
        k1_latency = round(random.uniform(24.0, 32.0), 1)
        k1_loss = round(random.uniform(2.5, 4.5), 2)
        k1_status = "SLA_VIOLATED"
    elif congested and ue1_priority >= 20:
        k1_prb = round(random.uniform(86.0, 91.0), 1)
        k1_latency = round(random.uniform(5.5, 7.8), 1)
        k1_loss = round(random.uniform(0.1, 0.3), 2)
        k1_status = "MITIGATED_BY_A1_POLICY"
    else:
        k1_prb = round(random.uniform(35.0, 50.0), 1)
        k1_latency = round(random.uniform(3.8, 5.5), 1)
        k1_loss = round(random.uniform(0.05, 0.15), 2)
        k1_status = "HEALTHY"

    return {
        "sla_target": {"slice": "slice-urllc", "max_latency_ms": 10.0},
        "cells": [
            {
                "cell_id": "kista_1",
                "ric_id": "ric1",
                "ue_id": "ue-001",
                "slice_id": "slice-urllc",
                "prb_utilization_pct": k1_prb,
                "latency_ms": k1_latency,
                "packet_loss_pct": k1_loss,
                "active_a1_priority": ue1_priority,
                "status": k1_status,
            },
            {
                "cell_id": "kista_2",
                "ric_id": "ric1",
                "ue_id": "ue-002",
                "slice_id": "slice-embb",
                "prb_utilization_pct": round(random.uniform(40.0, 55.0), 1),
                "latency_ms": round(random.uniform(18.0, 25.0), 1),
                "packet_loss_pct": 0.2,
                "active_a1_priority": get_active_priority_for_ue("ue-002"),
                "status": "HEALTHY",
            },
        ],
    }


class KPIHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/kpis":
            self._send_json(200, build_kpis())
        else:
            self._send_json(404, {"error": "use GET /kpis"})

    def do_POST(self):
        if self.path == "/scenario/congestion":
            STATE["congestion_kista_1"] = True
            self._send_json(200, {"scenario": "congestion_kista_1=ON"})
        elif self.path == "/scenario/normal":
            STATE["congestion_kista_1"] = False
            self._send_json(200, {"scenario": "congestion_kista_1=OFF"})
        else:
            self._send_json(404, {"error": "use POST /scenario/congestion ou /scenario/normal"})

    def log_message(self, format, *args):
        return  # silencia logs HTTP padrao para manter o terminal limpo


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8090), KPIHandler)
    print("[KPI Simulator] Rodando em http://localhost:8090/kpis (Ctrl+C para sair)")
    server.serve_forever()
