#!/usr/bin/env python3
"""K6/D2 — Gecikme maliyeti (SINCONF D2 metodolojisiyle aynı ilke):
N=1000 tekrar, birden fazla akış, p50/p95 raporlanır. SINCONF'ta
"yalniz yerel analiz" / "sidecar turu" / "tam HITL bekleme" ayrimina
karsilik gelen burdaki ayrim: ucuz-yol (R1 kisa devre), pahali-yol
(R1'in gercek TF2 lookup'i CALISIYOR), ve gateway-YOK taban cizgisi
(command_relay, hicbir mantik calistirmayan duz gecis).

Olcum KARA-KUTU'dur: /mission/command_raw'a YAYIN yapiyoruz, /mission/
command VEYA /mediation/rejected'te YANIT gelene kadar bekliyoruz.
Gercek ROS2/DDS transportu + gercek Python REGL degerlendirmesi olculuyor,
sentetik/izole bir mikro-benchmark degil.

Akislar:
  1. resume_allow_gateway  -- RESUME @ dusuk-risk stage=3, gateway ACIK.
     R1 kisa devre (CMD_START degil), yalniz R2/R3/R4 nitelik kontrolu.
     Guvenli: tekrar tekrar RESUME, ilk cagriden sonra executor'da no-op.
  2. resume_allow_relay    -- AYNI komut, gateway KAPALI (command_relay
     duz gecis). Sifir mantik -- taban cizgisi ("gateway olmasa ne kadar
     surerdi").
  3. skip_deny_gateway     -- SKIP, gateway ACIK. R4 HER ZAMAN reddeder;
     gateway bunu executor'e HIC iletmez (guvenli, tekrar edilebilir).
  4. start8_deny_gateway_expensive -- START target_stage=8, gateway ACIK.
     R4-a bunu reddeder AMA kod kisa devre YAPMADIGI icin R1'in GERCEK
     TF2 lookup'i (tf_buffer.lookup_transform) yine de calisir -- en
     pahali yol. R4 zaten reddettigi icin executor'e hic ulasmaz, guvenli.
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import StageCommand, MissionStatus

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gateway_gecikme_sonuclari.json"

CMD_START, CMD_PAUSE, CMD_RESUME, CMD_SKIP, CMD_ABORT, CMD_ESTOP = range(6)


class GecikmeGozlemci(Node):
    def __init__(self):
        super().__init__("robovis_gecikme_gozlemci")
        self._lock = threading.Lock()
        self._son_yanit_t = None
        self._son_yanit_konu = None
        self._raw = self.create_publisher(StageCommand, "/mission/command_raw", 10)
        self.create_subscription(StageCommand, "/mission/command", self._on_command, 50)
        self.create_subscription(
            __import__("std_msgs.msg", fromlist=["String"]).String,
            "/mediation/rejected", self._on_rejected, 50)
        self.create_subscription(MissionStatus, "/mission/status", lambda m: None, 10)

    def _on_command(self, msg):
        with self._lock:
            self._son_yanit_t = time.perf_counter()
            self._son_yanit_konu = "command"

    def _on_rejected(self, msg):
        with self._lock:
            self._son_yanit_t = time.perf_counter()
            self._son_yanit_konu = "rejected"

    def tek_deneme(self, command: int, target_stage: int, timeout: float = 2.0):
        with self._lock:
            self._son_yanit_t = None
            self._son_yanit_konu = None
        msg = StageCommand()
        msg.command = int(command)
        msg.target_stage = int(target_stage)
        t0 = time.perf_counter()
        self._raw.publish(msg)
        deadline = t0 + timeout
        while time.perf_counter() < deadline:
            with self._lock:
                if self._son_yanit_t is not None:
                    return (self._son_yanit_t - t0) * 1000.0, self._son_yanit_konu
            time.sleep(0.0003)
        return None, "timeout"


def ozet(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    n = len(s)
    def pct(p):
        idx = min(n - 1, int(round(p * (n - 1))))
        return s[idx]
    return {
        "n": n, "mean_ms": statistics.mean(s), "stdev_ms": statistics.pstdev(s) if n > 1 else 0.0,
        "min_ms": s[0], "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "max_ms": s[-1],
    }


def akis_calistir(gozlemci, ad, command, target_stage, n, beklenen_konu):
    print(f"=== {ad}: {n} deneme baslıyor ===", flush=True)
    degerler = []
    timeout_sayisi = 0
    yanlis_konu_sayisi = 0
    for i in range(n):
        ms, konu = gozlemci.tek_deneme(command, target_stage)
        if ms is None:
            timeout_sayisi += 1
            continue
        if konu != beklenen_konu:
            yanlis_konu_sayisi += 1
        degerler.append(ms)
        if (i + 1) % 200 == 0:
            print(f"  [{ad}] {i+1}/{n}", flush=True)
    s = ozet(degerler)
    s.update({"akis": ad, "timeout": timeout_sayisi, "beklenmeyen_konu": yanlis_konu_sayisi})
    print(f"=== {ad} SONUC: p50={s.get('p50_ms', 0):.2f}ms p95={s.get('p95_ms', 0):.2f}ms "
          f"n={s['n']} timeout={timeout_sayisi} ===", flush=True)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--mode", choices=("gateway", "relay"), required=True)
    args = ap.parse_args()

    rclpy.init()
    gozlemci = GecikmeGozlemci()
    thread = threading.Thread(target=rclpy.spin, args=(gozlemci,), daemon=True)
    thread.start()
    time.sleep(2.0)

    sonuclar = []
    try:
        if args.mode == "gateway":
            sonuclar.append(akis_calistir(
                gozlemci, "resume_allow_gateway", CMD_RESUME, 0, args.n, "command"))
            sonuclar.append(akis_calistir(
                gozlemci, "skip_deny_gateway", CMD_SKIP, 0, args.n, "rejected"))
            sonuclar.append(akis_calistir(
                gozlemci, "start8_deny_gateway_pahali_R1_TF_lookup", CMD_START, 8, args.n, "rejected"))
        else:
            sonuclar.append(akis_calistir(
                gozlemci, "resume_allow_relay_taban_cizgisi", CMD_RESUME, 0, args.n, "command"))
    finally:
        gozlemci.destroy_node()
        rclpy.shutdown()
        thread.join(timeout=5)

    onceki = []
    if OUT.exists():
        onceki = json.loads(OUT.read_text(encoding="utf-8")).get("akislar", [])
    onceki = [x for x in onceki if x["akis"] not in {s["akis"] for s in sonuclar}]
    tumu = onceki + sonuclar
    OUT.write_text(json.dumps({"schema": "gateway-gecikme/v1", "akislar": tumu},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCikti: {OUT}")


if __name__ == "__main__":
    main()
