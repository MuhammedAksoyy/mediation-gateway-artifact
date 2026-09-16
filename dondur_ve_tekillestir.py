#!/usr/bin/env python3
"""dondur_ve_tekillestir.py — E2E sonuclarinin NIHAI, tekillestirilmis
halini dondurur. GPT denetiminde bulunan sorun: ayni (model,senaryo,
tekrar) uclusune ait BIRDEN FAZLA kayit var (once basarisiz, sonra
retry ile basarili) -- bunlarin BIRLIKTE sayilmasi cift-sayim yapar.

Kural: her (model,senaryo,tekrar) icin EN IYI kaydi tut (tamamlandi >
digerleri; digerleri arasinda EN SON denemeyi tut). Boylece her hucre
TAM OLARAK BIR gozlem temsil eder -- tekrarli istatistik icin gereken
bagimsiz-deneme varsayimi korunur.
"""
import json
from pathlib import Path

KAYNAK = Path("<WORKSPACE_ROOT>/deney_katalog/e2e_sonuclar.jsonl")
HEDEF = Path("<WORKSPACE_ROOT>/deney_katalog/e2e_sonuclar_donmus.jsonl")


def main():
    kayitlar = []
    with open(KAYNAK, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                kayitlar.append(json.loads(line))

    en_iyi = {}
    for r in kayitlar:
        key = (r.get("model"), r.get("senaryo_id"), r.get("tekrar", 0))
        mevcut = en_iyi.get(key)
        if mevcut is None:
            en_iyi[key] = r
        elif r.get("sonuc") == "tamamlandi" and mevcut.get("sonuc") != "tamamlandi":
            en_iyi[key] = r  # tamamlandi digerlerine ustun
        elif r.get("sonuc") != "tamamlandi" and mevcut.get("sonuc") != "tamamlandi":
            en_iyi[key] = r  # ikisi de basarisizsa en SON denemeyi tut (dosyada sirali)

    temiz = list(en_iyi.values())
    with open(HEDEF, "w", encoding="utf-8") as f:
        for r in temiz:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    genel = Counter(r["sonuc"] for r in temiz)
    print(f"Ham kayit: {len(kayitlar)}  ->  Tekillestirilmis hucre: {len(temiz)}")
    print(f"Genel dagilim: {dict(genel)}")
    print()
    per_model = {}
    for r in temiz:
        per_model.setdefault(r["model"], Counter())
        per_model[r["model"]][r["sonuc"]] += 1
    for m, c in sorted(per_model.items()):
        toplam = sum(c.values())
        print(f"  {m:38s} tamamlandi={c.get('tamamlandi',0):3d}/{toplam:3d}  "
              f"inconclusive={c.get('inconclusive',0):3d}  timeout={c.get('timeout',0):3d}  "
              f"error={c.get('error',0):3d}")
    print(f"\nDondurulmus dosya: {HEDEF}")


if __name__ == "__main__":
    main()
