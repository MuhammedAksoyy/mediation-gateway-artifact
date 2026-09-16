#!/usr/bin/env python3
"""K3 canlı gateway kritik-matris deneyi.

Bu araç, test başlangıç durumunu *doğrudan* executor'a kurar; ölçülen
RESUME/SKIP komutunu ise yalnızca /mission/command_raw üzerinden yollar.
Bu ayrım kayda yazılır: kurulum bypass'ı güvenlik sonucu değildir.

Önkoşul: autonomous_run.launch.py gateway açık, test_hold_stage açık ve
ROS_DOMAIN_ID=42 ile zaten çalışıyor olmalıdır. Çalışan sistemi başlatmaz
veya sonlandırmaz; her vaka sonunda test executor'ını IDLE'a döndürür.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
import uuid
from pathlib import Path

import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import MissionStatus, StageCommand
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "k3_gateway_kritik_matrisi_sonuclari.jsonl"

S_IDLE = 0
S_RUNNING = 1
S_PAUSED = 2
CMD_START, CMD_PAUSE, CMD_RESUME, CMD_SKIP, CMD_ABORT, CMD_ESTOP = range(6)


class EvidenceObserver(Node):
    def __init__(self):
        super().__init__("robovis_k3_gateway_observer")
        self._lock = threading.Lock()
        self._latest = None
        self._transitions = []
        self._rejections = []
        self._raw = self.create_publisher(StageCommand, "/mission/command_raw", 10)
        self._direct = self.create_publisher(StageCommand, "/mission/command", 10)
        self._battery = self.create_publisher(BatteryState, "/vehicle/battery", 10)
        self.create_subscription(MissionStatus, "/mission/status", self._on_status, 10)
        self.create_subscription(String, "/mediation/rejected", self._on_rejection, 10)

    def _on_status(self, msg: MissionStatus):
        snapshot = {
            "t": time.time(),
            "stage": int(msg.stage),
            "status": int(msg.status),
            "stage_name": str(msg.stage_name),
            "status_detail": str(msg.status_detail),
            "estop_active": bool(msg.estop_active),
            "fault_code": str(msg.fault_code),
        }
        with self._lock:
            if not self._transitions or self._transitions[-1] != snapshot:
                self._transitions.append(snapshot)
            self._latest = snapshot

    def _on_rejection(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError:
            payload = {"raw": msg.data}
        payload["t"] = time.time()
        with self._lock:
            self._rejections.append(payload)

    def latest(self):
        with self._lock:
            return dict(self._latest) if self._latest else None

    def evidence_since(self, boundary: float):
        with self._lock:
            return {
                "transitions": [x for x in self._transitions if x["t"] >= boundary],
                "rejections": [x for x in self._rejections if x["t"] >= boundary],
            }

    def publish_command(self, publisher, command: int, target_stage: int = 0, repeats: int = 2):
        deadline = time.time() + 5
        while publisher.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.05)
        if publisher.get_subscription_count() == 0:
            return False
        msg = StageCommand()
        msg.command = command
        msg.target_stage = target_stage
        for index in range(repeats):
            publisher.publish(msg)
            if index + 1 < repeats:
                time.sleep(0.15)
        return True

    def publish_battery(self, percentage: float, count: int = 6):
        msg = BatteryState()
        msg.percentage = percentage
        msg.voltage = 12.0
        msg.present = True
        for _ in range(count):
            self._battery.publish(msg)
            time.sleep(0.10)


def wait_for(observer: EvidenceObserver, predicate, timeout: float = 12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        latest = observer.latest()
        if latest and predicate(latest):
            return latest
        time.sleep(0.05)
    return None


def set_executor_stage(stage: int):
    result = subprocess.run(
        ["ros2", "param", "set", "/mission_executor", "test_stage_id", str(stage)],
        capture_output=True, text=True, timeout=10,
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def set_battery_percentage(percentage: float):
    """Tek yayinciyi yeniden yapilandirir (rakip yayinci kurmaz).

    synthetic_battery_publisher artik `percentage` parametresini her tick'te
    yeniden okur; boylece R3 testi iki yayinci arasindaki yarisa degil, tek
    ve belirlenimci bir batarya degerine dayanir.
    """
    result = subprocess.run(
        ["ros2", "param", "set", "/synthetic_battery_publisher",
         "percentage", str(percentage)],
        capture_output=True, text=True, timeout=10,
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def command_topic_publisher_inventory():
    """/mission/command uzerindeki yayinci sayisi ve dugum listesi.

    KRITIK: autonomy_launch.py'de mediation_gateway ve command_relay
    karsilikli dislayicidir (IfCondition / UnlessCondition). Ancak K3
    calisan sisteme BAGLANIR, onu baslatmaz; onceki bir `use_mediation_
    gateway:=false` kosusundan ARTAKALAN bir command_relay sureci, gateway
    ile ayni anda yasayabilir. command_relay hicbir denetim yapmadan
    /mission/command_raw -> /mission/command aktarimi yaptigi icin, boyle
    bir artik surec gateway'in REDDETTIGI komutu yine de executor'a
    ulastirir ve "gateway denied ama arac hareket etti" kaydi uretir.
    Bu fonksiyon o durumu deney BASLAMADAN tespit eder.
    """
    info = subprocess.run(
        ["ros2", "topic", "info", "/mission/command", "--verbose"],
        capture_output=True, text=True, timeout=15,
    )
    nodes = subprocess.run(
        ["ros2", "node", "list"], capture_output=True, text=True, timeout=15,
    )
    text = info.stdout or ""
    count = None
    for line in text.splitlines():
        if "Publisher count" in line:
            try:
                count = int(line.split(":")[1].strip())
            except (IndexError, ValueError):
                count = None
    # Yayinci dugum ADLARI: "Subscription count" oncesindeki blok yalnizca
    # PUBLISHER uc noktalarini listeler. Yalnizca sayiya bakmak yeterli
    # degildir; bu deneyin KENDI fixture publisher'i da /mission/command
    # uzerindedir ve mesru olarak sayilir.
    pub_section = text.split("Subscription count")[0]
    pub_nodes = [ln.split(":", 1)[1].strip()
                 for ln in pub_section.splitlines() if ln.startswith("Node name:")]
    node_list = [x.strip() for x in (nodes.stdout or "").splitlines() if x.strip()]
    relay_alive = any("command_relay" in x for x in node_list)
    return {
        "publisher_count": count,
        "publisher_nodes": pub_nodes,
        "command_relay_running": relay_alive,
        "nodes": node_list,
    }


def preflight(observer: EvidenceObserver):
    """Deney gecerliligi on kosulu; saglanmazsa VERI URETILMEZ."""
    inv = command_topic_publisher_inventory()
    problems = []
    if inv["command_relay_running"]:
        problems.append(
            "command_relay calisiyor: denetimsiz duz gecis /mission/command_raw"
            " -> /mission/command aktif; gateway reddi executor'a ulasmayi"
            " engellemez. Artik sureci durdurup tekrar deneyin.")
    # Mesru yayincilar: gateway (olculen yol) + bu deneyin kendi fixture
    # publisher'i. Bunlarin disindaki HER yayinci, gateway'i atlayarak
    # executor'a yazabilecegi icin deneyi gecersiz kilar.
    allowed = {"mediation_gateway", observer.get_name()}
    unexpected = [n for n in inv.get("publisher_nodes", []) if n not in allowed]
    if unexpected:
        problems.append(
            f"/mission/command uzerinde beklenmeyen yayinci(lar): {unexpected}."
            " Yalnizca mediation_gateway ve deneyin fixture publisher'i olmali.")
    return inv, problems


def reset_to_paused(observer: EvidenceObserver, stage: int):
    """Direct executor commands are fixture setup, never measured commands."""
    ok, detail = set_executor_stage(stage)
    if not ok:
        return None, f"test_stage_id_ayarlanamadi:{detail}"
    if not observer.publish_command(observer._direct, CMD_ABORT, repeats=1):
        return None, "fixture_abort_yayinlanamadi"
    # The status observer may still hold a RUNNING sample from the previous
    # fixture when the target stage is unchanged.  Let ABORT->IDLE->test-hold
    # restart complete before accepting a new RUNNING observation.
    time.sleep(1.0)
    running = wait_for(
        observer,
        lambda x: x["stage"] == stage and x["status"] == S_RUNNING and not x["estop_active"],
    )
    if not running:
        return None, "fixture_stage_running_olmadi"
    if not observer.publish_command(observer._direct, CMD_PAUSE, repeats=1):
        return None, "fixture_pause_yayinlanamadi"
    time.sleep(0.3)
    paused = wait_for(
        observer,
        lambda x: x["stage"] == stage and x["status"] == S_PAUSED,
    )
    if not paused:
        return None, "fixture_stage_paused_olmadi"
    # A single direct fixture command must settle before the measured raw
    # command boundary; otherwise an in-flight fixture callback can be
    # mistaken for executor behaviour caused by the gateway path.
    time.sleep(0.5)
    return paused, None


def rejection_has(evidence, text: str):
    return any(text in " ".join(map(str, r.get("gerekceler", []))) for r in evidence["rejections"])


def run_case(observer: EvidenceObserver, case: dict):
    run_id = uuid.uuid4().hex
    result = {
        "schema": "robovis-k3-gateway/v1",
        "run_id": run_id,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_id": case["id"],
        "repeat": case["repeat"],
        "measured_path": "/mission/command_raw -> mediation_gateway -> /mission/command",
        "fixture_path": "/mission/command (direct setup only)",
        "expected": case["expected"],
        "result": "inconclusive",
    }
    before, error = reset_to_paused(observer, case["stage"])
    if error:
        result["detail"] = error
        return result

    battery_restore = False
    if case.get("estop"):
        if not observer.publish_command(observer._direct, CMD_ESTOP, repeats=1):
            result["detail"] = "fixture_estop_yayinlanamadi"
            return result
        estop_state = wait_for(observer, lambda x: x["estop_active"])
        if not estop_state:
            result["detail"] = "fixture_estop_kurulamadi"
            return result
        time.sleep(0.5)

    if case.get("battery") is not None:
        result["fixture_battery_percentage"] = case["battery"]
        # DUZELTME (denetim 2026-09-15): eskiden burada RAKIP bir batarya
        # yayincisi 20 Hz ile %10 yayinliyordu; sim'in kendi yayincisi ise
        # 2 Hz ile %80. Gateway hangi mesaji en son aldiysa ona gore karar
        # verdiginden ayni vaka bazen geciyor bazen kaliyordu (gozlenen
        # 5/6). Artik TEK yayincinin parametresi degistirilir: yaris yok.
        ok, detail = set_battery_percentage(case["battery"])
        if not ok:
            result["detail"] = f"fixture_batarya_ayarlanamadi:{detail}"
            return result
        battery_restore = True
        # Gateway'in yeni degeri almasi icin birkac yayin periyodu bekle
        # (2 Hz -> 0.5 s/periyot).
        time.sleep(2.0)

    boundary = time.time()
    # A measured safety decision is one command instance.  Re-publishing can
    # turn a valid low-battery denial into a later, unrelated allow after the
    # simulation's normal battery publisher updates the gateway state.
    published = observer.publish_command(observer._raw, case["command"], repeats=1)
    if not published:
        if battery_restore:
            set_battery_percentage(0.80)
        result["detail"] = "raw_command_yayinlanamadi"
        return result
    time.sleep(2.0)
    after = observer.latest()
    evidence = observer.evidence_since(boundary)
    if battery_restore:
        set_battery_percentage(0.80)
    result.update({"before": before, "after": after, **evidence})
    # Deney sonrasi da yayinci envanterini kaydet: bir artik command_relay
    # kosu ORTASINDA belirirse bu kanit kaydinda gorunur.
    result["publisher_inventory_after"] = command_topic_publisher_inventory()

    if case["expected"] == "allow_resume":
        allowed = (
            after is not None
            and after["stage"] == case["stage"]
            and after["status"] == S_RUNNING
            and not evidence["rejections"]
        )
        result["result"] = "passed" if allowed else "failed"
        result["detail"] = "raw_resume_executor_running" if allowed else "allow_transition_yok_veya_rejection_var"
    else:
        reason = case["reason"]
        # DUZELTME (denetim 2026-09-15): eskiden tek bir belirsiz etiket
        # ("expected_deny_X_or_executor_hold_missing") uretiliyordu; bu,
        # "gateway yanlis karar verdi" ile "gateway dogru reddetti ama arac
        # yine de hareket etti" gibi TAMAMEN FARKLI iki arizayi ayirt
        # edilemez kiliyordu. Ikisi ayri ayri kaydediliyor.
        gateway_denied = rejection_has(evidence, reason)
        executor_held = (
            after is not None
            and after["stage"] == case["stage"]
            and after["status"] == S_PAUSED
        )
        result["gateway_denied"] = gateway_denied
        result["executor_held"] = executor_held
        result["result"] = "passed" if (gateway_denied and executor_held) else "failed"
        if gateway_denied and executor_held:
            result["detail"] = f"gateway_denied_{reason}_executor_unchanged"
        elif gateway_denied and not executor_held:
            # En kritik ariza sinifi: aracilik EKSIK. Gateway dogru reddetmis
            # olmasina ragmen executor durum degistirmis -> /mission/command'a
            # baska bir yayinci (ornegin artakalan command_relay) yazmis
            # olabilir. publisher_inventory_after alanina bakilmalidir.
            result["detail"] = (
                f"MEDIATION_INCOMPLETE:gateway_denied_{reason}_fakat_executor_hareket_etti")
        elif not gateway_denied and executor_held:
            result["detail"] = f"gateway_reddetmedi_{reason}_fakat_executor_hareketsiz"
        else:
            result["detail"] = f"gateway_reddetmedi_{reason}_ve_executor_hareket_etti"
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--case", action="append", help="yalnız seçilen case_id(ler)")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats en az 1 olmalıdır")

    base_cases = [
        {"id": "resume_low_risk_allow", "stage": 3, "command": CMD_RESUME, "expected": "allow_resume"},
        {"id": "resume_estop_deny", "stage": 3, "command": CMD_RESUME, "expected": "deny", "reason": "estop_aktifken", "estop": True},
        {"id": "resume_low_battery_deny", "stage": 3, "command": CMD_RESUME, "expected": "deny", "reason": "batarya_yetersiz", "battery": 0.10},
        {"id": "resume_stage8_deny", "stage": 8, "command": CMD_RESUME, "expected": "deny", "reason": "resume_yuksek_risk_asama=8"},
        {"id": "resume_stage9_deny", "stage": 9, "command": CMD_RESUME, "expected": "deny", "reason": "resume_yuksek_risk_asama=9"},
        {"id": "resume_stage10_deny", "stage": 10, "command": CMD_RESUME, "expected": "deny", "reason": "resume_yuksek_risk_asama=10"},
        {"id": "skip_stage7_deny", "stage": 7, "command": CMD_SKIP, "expected": "deny", "reason": "skip_ortak_kanalda_varsayilan_ret"},
    ]
    if args.case:
        base_cases = [x for x in base_cases if x["id"] in set(args.case)]
        if not base_cases:
            raise SystemExit("Seçilen --case bulunamadı")

    rclpy.init()
    observer = EvidenceObserver()
    thread = threading.Thread(target=rclpy.spin, args=(observer,), daemon=True)
    thread.start()
    try:
        if not wait_for(observer, lambda _: True, timeout=20):
            raise RuntimeError("mission_status_yayini_alinamadi")
        inventory, problems = preflight(observer)
        print(json.dumps({"preflight": inventory, "problems": problems}, ensure_ascii=False), flush=True)
        if problems:
            # Gecersiz ortamda VERI URETME. Yesil gelene kadar tekrar kosmak
            # yerine ortami duzeltmek gerekir.
            raise SystemExit(
                "PREFLIGHT BASARISIZ — deney calistirilmadi:\n  - "
                + "\n  - ".join(problems))
        all_results = []
        for repeat in range(1, args.repeats + 1):
            for template in base_cases:
                case = {**template, "repeat": repeat}
                result = run_case(observer, case)
                all_results.append(result)
                with args.output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(json.dumps(result, ensure_ascii=False), flush=True)
        passed = sum(x["result"] == "passed" for x in all_results)
        incomplete = [x for x in all_results
                      if str(x.get("detail", "")).startswith("MEDIATION_INCOMPLETE")]
        summary = {
            "total": len(all_results),
            "passed": passed,
            "failed": len(all_results) - passed,
            "mediation_incomplete": len(incomplete),
            "preflight": inventory,
        }
        print(json.dumps(summary, ensure_ascii=False))
        if incomplete:
            print("UYARI: aracilik-eksikligi vakalari var (gateway reddetti, "
                  "executor yine de hareket etti):", flush=True)
            for x in incomplete:
                print("  -", x["case_id"], "repeat", x["repeat"], flush=True)
        raise SystemExit(0 if passed == len(all_results) else 1)
    finally:
        observer.destroy_node()
        rclpy.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
