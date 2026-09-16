#!/usr/bin/env python3
"""e2e_harness.py — RoboVis plani Asama 0a-3.

Onceki `e2e_multi_model.sh`'in guvenilirlik hatasini (yeni kayit gelmezse
"tail -n 1" ile ONCEKI senaryonun kaydini tekrar yazma) DUZELTIR:
  - Her istek /mission/nl_task metnine "[RUN:<id>] " oneki ile gonderilir.
  - Planlayici kaydi, JSONL dosyasinin byte-offset'i takip edilerek yalnizca
    o run_id'ye ait YENI satirdan okunur.
  - Gateway karari, gateway log dosyasinin byte-offset'i ile ayni sekilde
    yalnizca komut gonderiminden SONRAKI yeni satirlardan okunur.
  - Executor'in GERCEK son durumu /mission/status'tan once/sonra okunup
    katalogdaki beklenenle karsilastirilir.
  - Bu UC kanittan (planlayici kaydi + gateway karari + executor durumu)
    biri eksikse sonuc "inconclusive"/"timeout" olarak isaretlenir; asla
    sessizce basari/basarisizliga cevrilmez (RoboVis plani Bolum 0, madde 2).

150 kosunun (15 senaryo x 10 model) bir ORAN deneyi DEGIL, KAPSAMA/
ENTEGRASYON testi oldugu unutulmamali (Bolum B). Oran iddialari yalnizca
tekrarli standalone korpustan (sonuclar_v2.json) gelir.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml

import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import MissionStatus, StageCommand

KLON = Path("<WORKSPACE_ROOT>")
KATALOG_YOLU = KLON / "deney_katalog" / "scenario_catalog.yaml"
KAYIT_YOLU = Path.home() / "llm_mission_planner_kayitlari.jsonl"
SONUC_YOLU = KLON / "deney_katalog" / "e2e_sonuclar.jsonl"

MODELLER = [
    ("mistral-nemo:12b", "ollama"),
    ("mistral:7b-instruct-q4_K_M", "ollama"),
    ("llama3:latest", "ollama"),
    ("gemma3:12b", "ollama"),
    ("gemma3:4b", "ollama"),
    ("aya-expanse:8b", "ollama"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia"),
    ("openai/gpt-oss-120b", "nvidia"),
    ("deepseek-ai/deepseek-v4-flash-0731", "nvidia"),
]
# NOT: moonshotai/kimi-k3, NVIDIA API'de kalici HTTP 429 (rate limit)
# verdigi icin (2026-08-30 tam kosuda 12/12 deneme basarisiz) katalogdan
# cikarilip deepseek ile degistirildi -- kullanici onayi. "deepseek-ai/
# deepseek-v3.1" GECERSIZ model adi cikti (404) -- NVIDIA API'de
# mevcut olanlar: deepseek-coder-6.7b-instruct, deepseek-v4-flash-0731,
# deepseek-v4-pro-0813. v4-pro asiri yavas (60sn+ curl timeout), flash
# saniyeler icinde yanit verdi -- flash secildi.

PLANLAYICI_TIMEOUT_S = 90
GATEWAY_TIMEOUT_S = 30
DURUM_TIMEOUT_S = 8


def calistir(cmd, timeout=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def setsid_baslat(cmd_str, logfile_path):
    """Yeni bir process group/session icinde baslatir; donen PID ayni
    zamanda PGID'dir (setsid ile). Yalnizca bu PGID'ye sinyal gonderilerek
    temizlenir -- isim deseniyle pkill ASLA kullanilmaz."""
    logf = open(logfile_path, "wb")
    # DIKKAT: shell=True varsayilan olarak /bin/sh kullanir; 'source' bash'e
    # ozgudur, sh'de yoktur ('source: not found' hatasiyla TUM komut sessizce
    # basarisiz olur). executable='/bin/bash' ile acikca bash zorlanir.
    p = subprocess.Popen(
        cmd_str, shell=True, executable="/bin/bash",
        stdout=logf, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid, cwd=str(KLON))
    return p, logf


def pgid_oldur(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def dosya_boyu(path):
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0


def yeni_planlayici_kaydi_ara(offset, run_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(KAYIT_YOLU, "r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("run_id") == run_id:
                        return rec
        except FileNotFoundError:
            pass
        time.sleep(1.0)
    return None


def yeni_gateway_karari_ara(gw_log_path, offset, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(gw_log_path, "rb") as f:
                f.seek(offset)
                veri = f.read().decode("utf-8", errors="ignore")
            for line in veri.splitlines():
                if "ONAYLANDI" in line or "REDDEDİLDİ" in line:
                    yeni_offset = offset + len(veri.encode("utf-8"))
                    karar = "ALLOW" if "ONAYLANDI" in line else "DENY"
                    return karar, line.strip(), yeni_offset
        except FileNotFoundError:
            pass
        time.sleep(1.0)
    return None, None, offset


class DurumDinleyici:
    """UCUNCU DUZELTME (kok neden): 'ros2 topic echo --once' her cagrida
    YENI bir DDS katilimcisi kurup yikiyordu -- ayni sistemde ayni anda
    bazen 1sn'de, bazen 8sn+ zaman asimina ugrayarak basarisiz oluyordu
    (deneme sayisini/suresini artirmak bunu SANSA birakiyordu, cozmuyordu).
    Dogru cozum: harness'in KENDI surecinde, TUM kosu boyunca (her senaryo
    ve her model-basi sim yeniden baslatmasi dahil) TEK bir kalici rclpy
    abonesi acmak -- kesif maliyeti bir kere odenir, sonraki her okuma
    yalnizca son alinan mesaji bellekten dondurur (yeni process/kesif yok).
    Sim her yeniden baslatildiginda ESKI yayinci kaybolur, DDS otomatik
    olarak YENI mission_executor'a yeniden eslesir -- bu arka planda,
    engellemeden gerceklesir."""

    def __init__(self):
        self._kilit = threading.Lock()
        self._son_mesaj = None
        self._son_alim_zamani = 0.0
        self._gecisler = []
        self._son_imza = None
        rclpy.init(args=None)
        self._node = Node("e2e_harness_durum_dinleyici")
        self._node.create_subscription(
            MissionStatus, "/mission/status", self._geri_cagri, 10)
        self._komut_pub = self._node.create_publisher(StageCommand, "/mission/command", 10)
        self._executor_thread = threading.Thread(
            target=rclpy.spin, args=(self._node,), daemon=True)
        self._executor_thread.start()

    def _geri_cagri(self, msg):
        with self._kilit:
            imza = (int(msg.stage), int(msg.status), bool(msg.estop_active),
                    str(msg.status_detail))
            if imza != self._son_imza:
                self._gecisler.append({
                    "t": time.time(), "stage": int(msg.stage),
                    "status": int(msg.status),
                    "stage_name": str(msg.stage_name),
                    "estop_active": bool(msg.estop_active),
                    "status_detail": str(msg.status_detail),
                })
                self._son_imza = imza
            self._son_mesaj = msg
            self._son_alim_zamani = time.time()

    def gecisleri_al(self, referans_zaman=0.0):
        """Return time-stamped stage/status transitions after a test boundary."""
        with self._kilit:
            return [dict(x) for x in self._gecisler if x["t"] >= referans_zaman]

    def gecisleri_sifirla(self):
        with self._kilit:
            self._gecisler.clear()
            self._son_imza = None

    def komut_yayinla_dogrudan(self, command, target_stage=0):
        """Publish through the persistent ROS node to avoid CLI discovery races."""
        deadline = time.time() + 5.0
        while self._komut_pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.05)
        if self._komut_pub.get_subscription_count() == 0:
            return False
        msg = StageCommand()
        msg.command = int(command)
        msg.target_stage = int(target_stage)
        # RELIABLE delivery: publish twice across two spin intervals so a
        # freshly discovered executor cannot miss the transition command.
        self._komut_pub.publish(msg)
        time.sleep(0.1)
        self._komut_pub.publish(msg)
        return True

    def durum_oku(self, referans_zaman=0.0, zaman_asimi=DURUM_TIMEOUT_S):
        """referans_zaman'dan SONRA alinmis bir mesaj bekler -- sim yeniden
        baslatilmadan onceki eski/bayat mesaji YANLISLIKLA taze sanmamak icin
        (planlayici/gateway kanitlarindaki byte-offset yaklasimiyla ayni ilke)."""
        deadline = time.time() + zaman_asimi
        while time.time() < deadline:
            with self._kilit:
                if self._son_alim_zamani > referans_zaman and self._son_mesaj is not None:
                    m = self._son_mesaj
                    return {
                        "stage": m.stage, "stage_name": m.stage_name,
                        "status": m.status, "status_detail": m.status_detail,
                        "progress": m.progress, "elapsed_time": m.elapsed_time,
                        "estop_active": m.estop_active,
                        "fault_code": m.fault_code, "fault_detail": m.fault_detail,
                    }
            time.sleep(0.1)
        return None

    def kapat(self):
        rclpy.shutdown()
        self._executor_thread.join(timeout=5)
        try:
            self._node.destroy_node()
        except Exception:
            pass


_DINLEYICI = None


def mission_status_oku(referans_zaman=0.0, zaman_asimi=DURUM_TIMEOUT_S):
    if _DINLEYICI is None:
        return None
    return _DINLEYICI.durum_oku(referans_zaman, zaman_asimi)


def komut_yayinla(run_id, gorev):
    """Basarisiz/timeout olursa False doner -- ASLA istisna firlatip tum
    harness'i cokertmez; senaryo_calistir bunu 'timeout' sonucuna cevirir.

    DUZELTME (duman testi bulgusu): elle "'" -> "\\'" kacisi, kesme isareti
    iceren gorev metinlerinde (or. Y2: "E-STOP'u") ros2 CLI'nin YAML
    ayristiricisini bozup sessiz timeout'a yol aciyordu. yaml.dump ile
    duzgun/guvenli YAML uretimi kullanilir."""
    metin = f"[RUN:{run_id}] {gorev}"
    yaml_govde = yaml.dump({"data": metin}, default_flow_style=True,
                            allow_unicode=True).strip()
    try:
        calistir([
            "ros2", "topic", "pub", "--once", "/mission/nl_task",
            "std_msgs/msg/String", yaml_govde,
        ], timeout=15)
        return True
    except subprocess.TimeoutExpired:
        return False


DURUM_KURULUM_TIMEOUT_S = 90


def durum_kur(senaryo):
    """Kataloğun baslangic_durumu'nu GERCEKTEN kurar. CANLI kanit (2026-08-30
    duman testi): NM1 (zararsiz, ALLOW beklenen) bile IDLE'dan RESUME
    denedigi icin R4 tarafindan yanlislikla reddedildi; K2/K3/Y1-Y3 gibi
    RUNNING veya STOP_WAIT+ESTOP gerektiren senaryolar taze sim IDLE'dan
    baslatildiginda kataloğun tanimladigi kosuldan TAMAMEN farkli bir
    durumu test ediyordu. Gateway'i BILEREK atlayip dogrudan /mission/command'a
    yayinlar (bu, TEST EDILEN mediation etkilesiminin bir parcasi DEGIL,
    yalnizca baslangic sahnesini kurma adimidir -- gercek senaryo komutu
    hala /mission/nl_task -> LLM -> gateway -> /mission/command yolundan
    gecer). Basarisiz olursa (or. nav suresi asimi) senaryo 'inconclusive'
    doner, asla sessizce yanlis baslangic durumuyla devam etmez."""
    bd = senaryo.get("baslangic_durumu", {})
    hedef_status = bd.get("mission_status", "IDLE")
    if hedef_status == "IDLE":
        return True  # taze sim zaten IDLE -- kurulum gerekmiyor

    hedef_stage = bd.get("stage")
    ref = time.time()
    try:
        calistir([
            "ros2", "topic", "pub", "--once", "/mission/command",
            "karamuhafiz_msgs/msg/StageCommand",
            f"{{command: 0, target_stage: {hedef_stage}}}",
        ], timeout=10)
    except subprocess.TimeoutExpired:
        return False

    deadline = time.time() + DURUM_KURULUM_TIMEOUT_S
    ulasti = False
    while time.time() < deadline:
        d = mission_status_oku(referans_zaman=ref, zaman_asimi=3)
        if d and d.get("stage") == hedef_stage:
            ulasti = True
            break
        time.sleep(1.0)
    if not ulasti:
        return False

    if hedef_status == "STOP_WAIT" and bd.get("estop_active"):
        ref2 = time.time()
        try:
            calistir([
                "ros2", "topic", "pub", "--once", "/mission/command",
                "karamuhafiz_msgs/msg/StageCommand",
                "{command: 5, target_stage: 0}",
            ], timeout=10)
        except subprocess.TimeoutExpired:
            return False
        deadline = time.time() + 10
        while time.time() < deadline:
            d = mission_status_oku(referans_zaman=ref2, zaman_asimi=3)
            if d and d.get("estop_active"):
                return True
            time.sleep(0.5)
        return False
    return True


def senaryo_calistir(senaryo, model, provider, gw_log_path):
    run_id = uuid.uuid4().hex[:12]

    if not durum_kur(senaryo):
        return {
            "senaryo_id": senaryo["id"], "model": model, "provider": provider,
            "run_id": run_id, "sonuc": "inconclusive",
            "detay": "baslangic_durumu_kurulamadi",
        }

    once_durum = mission_status_oku(referans_zaman=0.0, zaman_asimi=3.0)

    planlayici_offset_once = dosya_boyu(KAYIT_YOLU)
    gw_offset_once = dosya_boyu(gw_log_path)

    komut_oncesi_zaman = time.time()
    yayin_basarili = komut_yayinla(run_id, senaryo["dogal_dil_istemi"])
    if not yayin_basarili:
        return {
            "senaryo_id": senaryo["id"], "model": model, "provider": provider,
            "run_id": run_id, "sonuc": "timeout",
            "detay": "komut_yayinlanamadi",
        }

    planlayici_kaydi = yeni_planlayici_kaydi_ara(
        planlayici_offset_once, run_id, PLANLAYICI_TIMEOUT_S)
    if planlayici_kaydi is None:
        return {
            "senaryo_id": senaryo["id"], "model": model, "provider": provider,
            "run_id": run_id, "sonuc": "timeout",
            "detay": "planlayici_kaydi_bulunamadi",
        }

    gw_karari, gw_satiri, _ = yeni_gateway_karari_ara(
        gw_log_path, gw_offset_once, GATEWAY_TIMEOUT_S)
    if gw_karari is None:
        return {
            "senaryo_id": senaryo["id"], "model": model, "provider": provider,
            "run_id": run_id, "sonuc": "inconclusive",
            "detay": "gateway_karari_bulunamadi",
            "planlayici_kaydi": planlayici_kaydi,
        }

    time.sleep(1.0)  # executor'in durumu isleme suresi
    sonra_durum = mission_status_oku(referans_zaman=komut_oncesi_zaman,
                                      zaman_asimi=DURUM_TIMEOUT_S)
    if sonra_durum is None:
        # DUZELTME: uc kanittan (planlayici + gateway + executor durumu)
        # ucuncusu okunamadiysa "tamamlandi" DENMEZ -- dosya basindaki
        # dokumante edilen kural ("asla sessizce basari/basarisizliga
        # cevrilmez") artik gercekten uygulanir.
        return {
            "senaryo_id": senaryo["id"], "model": model, "provider": provider,
            "run_id": run_id, "sonuc": "inconclusive",
            "detay": "executor_durumu_okunamadi",
            "planlayici_kaydi": planlayici_kaydi,
            "gateway_karari": gw_karari,
            "gateway_satiri": gw_satiri,
            "once_durum": once_durum,
        }

    return {
        "senaryo_id": senaryo["id"], "model": model, "provider": provider,
        "run_id": run_id, "sonuc": "tamamlandi",
        "planlayici_kaydi": planlayici_kaydi,
        "gateway_karari": gw_karari,
        "gateway_satiri": gw_satiri,
        "once_durum": once_durum,
        "sonra_durum": sonra_durum,
        "beklenen_gateway_karari": senaryo.get("beklenen_gateway_karari"),
    }


TEKRAR_B = 3  # Aşama B'nin istatistiksel (tekrarlı) genişletmesi --
# kullanıcı talebiyle (2026-08-30): tekrarsız 150-run zaten kapsama
# kanıtı verdi (132/150, 0 bypass), ama "0 atlatma" iddiasını güven
# aralığıyla desteklemek için her hücre TEKRAR_B kez koşuluyor.


def zaten_tamamlanan_oku():
    """Devam-edilebilirlik: onceki bir kosu yarida kesilirse ayni
    (model, senaryo_id, tekrar) tekrar kosulmasin -- yalnizca GERCEKTEN
    'tamamlandi' olanlar atlanir (inconclusive/timeout/error tekrar
    denenir, cunku tam olarak bunlari doldurmak istiyoruz)."""
    tamamlanan = set()
    if not SONUC_YOLU.exists():
        return tamamlanan
    with open(SONUC_YOLU, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("sonuc") == "tamamlandi":
                tekrar = r.get("tekrar", 0)  # eski kayitlarda alan yok -> tekrar=0 sayilir
                tamamlanan.add((r.get("model"), r.get("senaryo_id"), tekrar))
    return tamamlanan


def main():
    global _DINLEYICI
    katalog = yaml.safe_load(open(KATALOG_YOLU, encoding="utf-8"))
    senaryolar = katalog["senaryolar"]

    yalniz_model = sys.argv[1] if len(sys.argv) > 1 else None
    tamamlanan = zaten_tamamlanan_oku()

    SONUC_YOLU.parent.mkdir(parents=True, exist_ok=True)
    if not SONUC_YOLU.exists():
        SONUC_YOLU.touch()

    # Tum kosu boyunca TEK kalici abone (bkz. DurumDinleyici docstring) --
    # her senaryo/model sim'i yeniden baslatsa bile bu abone kapanmiyor,
    # DDS otomatik olarak yeni yayinciya yeniden eslesiyor.
    _DINLEYICI = DurumDinleyici()

    # DUZELTME (duman testi bulgusu): senaryolar model-basina TEK bir
    # paylasilan oturumda kosuluyordu -- bir senaryonun gercek etkisi
    # (or. K3'un E-STOP'u GERCEKTEN tetiklemesi) sonraki TUM senaryolara
    # sizip alakasiz red/onaylara yol aciyordu. Artik HER senaryo icin
    # sim tamamen yeniden baslatiliyor (yavas ama kesin temiz -- plan
    # Bolum 0a'da onceden kabul edilen sure maliyeti).
    for model, provider in MODELLER:
        if yalniz_model and model != yalniz_model:
            continue
        print(f"\n=== MODEL: {model} ({provider}) ===", flush=True)

        for senaryo in senaryolar:
            for tekrar in range(TEKRAR_B):
                if (model, senaryo["id"], tekrar) in tamamlanan:
                    continue
                if KAYIT_YOLU.exists():
                    KAYIT_YOLU.unlink()

                etiket = f"{model.replace('/', '_').replace(':', '_')}_{senaryo['id']}_{tekrar}"
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
                proc, logf = setsid_baslat(cmd, launch_log)
                time.sleep(35)  # autonomy_start_delay (20s) + gazebo/gateway boot payi

                try:
                    sonuc = senaryo_calistir(senaryo, model, provider, launch_log)
                except Exception as e:
                    sonuc = {
                        "senaryo_id": senaryo["id"], "model": model,
                        "provider": provider, "sonuc": "error", "detay": str(e),
                    }
                sonuc["tekrar"] = tekrar

                pgid_oldur(proc.pid)
                logf.close()
                calistir(["bash", "<WORKSPACE_ROOT>/cleanup_klon.sh"],
                          timeout=20)

                with open(SONUC_YOLU, "a", encoding="utf-8") as f:
                    f.write(json.dumps(sonuc, ensure_ascii=False) + "\n")
                print(f"  {senaryo['id']:5s} #{tekrar} -> {sonuc['sonuc']}"
                      f" (gateway={sonuc.get('gateway_karari','-')})", flush=True)

    _DINLEYICI.kapat()
    print("\n=== TAMAMLANDI ===", flush=True)


if __name__ == "__main__":
    main()
