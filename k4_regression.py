#!/usr/bin/env python3
"""K4 policy regression tests without launching Gazebo.

These tests exercise the gateway's pure rule methods with synthetic,
time-controlled telemetry. They are complementary to E2E tests and do not
claim physical safety.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from karamuhafiz_msgs.msg import MissionStatus, StageCommand
sys.path.insert(0, str(ROOT / "../src/karamuhafiz_autonomy/scripts"))
from mediation_gateway import MediationGateway


def gateway():
    g = MediationGateway.__new__(MediationGateway)
    g._status_timeout = 2.0
    g._battery_timeout = 5.0
    g._battery_min = 0.20
    g._son_status_stamp = time.monotonic()
    g._battery_stamp = time.monotonic()
    g._battery_fraction = 0.80
    g._dusuk_risk_asamalar = frozenset(range(1, 8))
    g._son_status = MissionStatus()
    g._son_status.stage = 7
    g._son_status.estop_active = False
    return g


def check(name, condition, detail):
    return {"id": name, "pass": bool(condition), "detail": detail}


def main():
    cases = []
    g = gateway()
    g._son_status.stage = 10
    cases.append(check("stage10_resume_deny", not g._r4_sira_yetki(StageCommand.CMD_RESUME)[0], "stage=10"))
    g._son_status.stage = 7
    cases.append(check("stage7_resume_allow", g._r4_sira_yetki(StageCommand.CMD_RESUME)[0], "stage=7"))
    g._son_status_stamp = time.monotonic() - 3.0
    cases.append(check("stale_status_resume_deny", not g._r2_estop_yetkisi_var_mi(StageCommand.CMD_RESUME)[0], "status age>2s"))
    g._son_status_stamp = time.monotonic()
    g._son_status.estop_active = True
    cases.append(check("estop_resume_deny", not g._r2_estop_yetkisi_var_mi(StageCommand.CMD_RESUME)[0], "estop=true"))
    g._son_status.estop_active = False
    g._battery_fraction = 0.10
    cases.append(check("low_battery_start_deny", not g._r3_enerji_yeterli_mi(StageCommand.CMD_START)[0], "battery=.10"))
    cases.append(check("low_battery_abort_allow", g._r3_enerji_yeterli_mi(StageCommand.CMD_ABORT)[0], "abort exception"))
    g._battery_fraction = None
    cases.append(check("missing_battery_fail_closed", not g._r3_enerji_yeterli_mi(StageCommand.CMD_RESUME)[0], "battery missing"))
    cases.append(check("start_target0_allow", g._r4_start_tek_asama_modu(StageCommand.CMD_START, 0)[0], "normal start"))
    cases.append(check("start_target1_allow", g._r4_start_tek_asama_modu(StageCommand.CMD_START, 1)[0], "normal stage 1"))
    cases.append(check("start_target8_deny", not g._r4_start_tek_asama_modu(StageCommand.CMD_START, 8)[0], "restricted jump"))
    cases.append(check("abort_target8_not_r4_denied", g._r4_start_tek_asama_modu(StageCommand.CMD_ABORT, 8)[0], "target ignored for abort"))
    result = {"schema": "k4-regression/v1", "total": len(cases),
              "passed": sum(c["pass"] for c in cases), "cases": cases}
    (ROOT / "k4_regression_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
