#!/usr/bin/env python3
"""backfill.py — eksik/basarisiz (model, senaryo) ciftlerini tamamlar.

150-run'daki iki tip eksik icin:
  1) Gecici gateway-zamanlama kaciraklari (GATEWAY_TIMEOUT_S artik 15->30) --
     AYNI modelle tekrar denenir.
  2) kimi-k3'un kalici NVIDIA rate-limit (429) nedeniyle bos kalan 15
     senaryosu -- YENI model (deepseek-ai/deepseek-v3.1) ile doldurulur.

Sonuclar AYNI e2e_sonuclar.jsonl'a EKLENIR (append) -- 150-run'un basarili
120 satiri KORUNUR, yeniden kosulmaz.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import e2e_harness as eh

# DUZELTME (2026-08-30 sabah): dun geceki kismi backfill'de bazilari
# ZATEN duzeldi (nemotron-nano/G2, nemotron-super/NM2+G1) -- yalnizca
# HALA inconclusive olan (bugunku dosya taramasiyla dogrulandi) kaldi.
# (model, provider, senaryo_id) -- ayni modelle tekrar denenecekler
TEKRAR_AYNI_MODEL = [
    ("gemma3:4b", "ollama", "O1"),
    ("gemma3:4b", "ollama", "O2"),
    ("gemma3:4b", "ollama", "O3"),
    ("aya-expanse:8b", "ollama", "G2"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia", "O2"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia", "Y3"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia", "O2"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia", "O3"),
    ("openai/gpt-oss-120b", "nvidia", "O3"),
    ("openai/gpt-oss-120b", "nvidia", "G1"),
    ("openai/gpt-oss-120b", "nvidia", "Y3"),
]

# kimi-k3'un yerine gecen model -- TUM 15 senaryo (dun "deepseek-ai/
# deepseek-v3.1" GECERSIZ model adiyla denendi, 3 kayit silinip
# duzeltildi -- dogru ad: deepseek-v4-flash-0731)
YENI_MODEL = ("deepseek-ai/deepseek-v4-flash-0731", "nvidia")


def calistir_tek(model, provider, senaryo_id, senaryolar_by_id):
    senaryo = senaryolar_by_id[senaryo_id]
    etiket = f"backfill_{model.replace('/', '_').replace(':', '_')}_{senaryo_id}"
    launch_log = f"/tmp/e2e_harness_{etiket}.log"
    cmd = (
        "source /opt/ros/humble/setup.bash && "
        "source <WORKSPACE_ROOT>/install/setup.bash && "
        "export ROS_DOMAIN_ID=42 && export ROS_LOCALHOST_ONLY=1 && "
        "ros2 launch karamuhafiz_bringup autonomous_run.launch.py "
        "use_mediation_gateway:=true use_llm_mission_planner:=true "
        "use_gui:=false "
        f"llm_provider:={provider} llm_model:={model}"
    )
    proc, logf = eh.setsid_baslat(cmd, launch_log)
    time.sleep(35)
    try:
        sonuc = eh.senaryo_calistir(senaryo, model, provider, launch_log)
    except Exception as e:
        sonuc = {"senaryo_id": senaryo_id, "model": model, "provider": provider,
                  "sonuc": "error", "detay": str(e)}
    eh.pgid_oldur(proc.pid)
    logf.close()
    eh.calistir(["bash", "<WORKSPACE_ROOT>/cleanup_klon.sh"], timeout=20)
    with open(eh.SONUC_YOLU, "a", encoding="utf-8") as f:
        f.write(json.dumps(sonuc, ensure_ascii=False) + "\n")
    print(f"  [backfill] {model} {senaryo_id} -> {sonuc['sonuc']}"
          f" (gateway={sonuc.get('gateway_karari', '-')})", flush=True)


def main():
    katalog = eh.yaml.safe_load(open(eh.KATALOG_YOLU, encoding="utf-8"))
    senaryolar_by_id = {s["id"]: s for s in katalog["senaryolar"]}

    eh._DINLEYICI = eh.DurumDinleyici()

    print(f"=== BACKFILL: {len(TEKRAR_AYNI_MODEL)} ayni-model tekrar + "
          f"15 deepseek (kimi yerine) ===", flush=True)

    for model, provider, sid in TEKRAR_AYNI_MODEL:
        calistir_tek(model, provider, sid, senaryolar_by_id)

    model, provider = YENI_MODEL
    print(f"\n=== YENI MODEL (kimi-k3 yerine): {model} ===", flush=True)
    for sid in senaryolar_by_id:
        calistir_tek(model, provider, sid, senaryolar_by_id)

    eh._DINLEYICI.kapat()
    print("\n=== BACKFILL TAMAMLANDI ===", flush=True)


if __name__ == "__main__":
    main()
