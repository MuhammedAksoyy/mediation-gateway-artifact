#!/usr/bin/env python3
"""deneme_gecmisi.py — GPT denetimi 2. tur, madde 8: 'yalnizca en-iyi
kayit' tablosu operasyonel kararliligi oldugundan iyi gosterebilir.
Bu script, HAM e2e_sonuclar.jsonl'dan (dondurulmus/tekillestirilmis
DEGIL) her (model,senaryo,tekrar) hucresi icin deneme sayisini, ilk
denemenin sonucunu ve kac. denemede basariya ulasildigini (varsa)
ayri bir tabloya cikarir -- kapsama iddiasinin (359/434) ARKASINDAKI
gercek operasyonel maliyeti (kac retry gerekti) seffaf gosterir.
"""
import json
from collections import defaultdict
from pathlib import Path

KAYNAK = Path("<WORKSPACE_ROOT>/deney_katalog/e2e_sonuclar.jsonl")
HEDEF = Path("<WORKSPACE_ROOT>/deney_katalog/deneme_gecmisi.jsonl")


def main():
    denemeler = defaultdict(list)
    with open(KAYNAK, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (r.get("model"), r.get("senaryo_id"), r.get("tekrar", 0))
            denemeler[key].append(r["sonuc"])

    ozet = []
    coklu_deneme_sayisi = 0
    for (model, sid, tekrar), sonuclar in sorted(denemeler.items()):
        deneme_sayisi = len(sonuclar)
        if deneme_sayisi > 1:
            coklu_deneme_sayisi += 1
        ilk_basarili_index = next(
            (i for i, s in enumerate(sonuclar) if s == "tamamlandi"), None)
        ozet.append({
            "model": model, "senaryo_id": sid, "tekrar": tekrar,
            "deneme_sayisi": deneme_sayisi,
            "deneme_sirasi_sonuclari": sonuclar,
            "kacinci_denemede_basarili": (ilk_basarili_index + 1
                                           if ilk_basarili_index is not None else None),
        })

    with open(HEDEF, "w", encoding="utf-8") as f:
        for o in ozet:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"Toplam hucre: {len(ozet)}")
    print(f"Birden fazla deneme gerektiren hucre: {coklu_deneme_sayisi}")
    hic_basarisiz = sum(1 for o in ozet if o["kacinci_denemede_basarili"] is None)
    print(f"Hicbir denemede basarili olamayan hucre: {hic_basarisiz}")
    print(f"Cikti: {HEDEF}")


if __name__ == "__main__":
    main()
