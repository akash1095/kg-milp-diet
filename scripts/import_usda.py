"""Replace the approximate nutrient values in data/foods.csv with USDA FoodData Central values.

1. Download the FDC CSV bundle (SR Legacy or Foundation Foods) from
   https://fdc.nal.usda.gov/download-datasets.html and unzip it.
2. Fill the fdc_id column in data/foods.csv for each food (search the FDC website).
3. Run:  python scripts/import_usda.py --fdc-dir path/to/FoodData_Central_csv

Rows without an fdc_id keep their current (approximate) values; the script
prints which rows were updated and which nutrients were missing.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

# FDC nutrient numbers (nutrient.csv "nutrient_nbr") for each column in foods.csv
NUTRIENT_NBR = {
    "energy_kcal": "208", "protein_g": "203", "carbs_g": "205", "fat_g": "204",
    "sat_fat_g": "606", "fiber_g": "291", "sugar_g": "269", "sodium_mg": "307",
    "potassium_mg": "306", "phosphorus_mg": "305", "calcium_mg": "301",
    "iron_mg": "303", "vitamin_k_ug": "430",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fdc-dir", required=True)
    ap.add_argument("--foods", default=str(Path(__file__).resolve().parents[1] / "data" / "foods.csv"))
    args = ap.parse_args()
    fdc = Path(args.fdc_dir)

    with open(fdc / "nutrient.csv", newline="", encoding="utf-8") as fh:
        # exact nutrient numbers only: "205.2" (carbohydrate by summation) must not replace "205"
        nbr_to_id = {r["nutrient_nbr"].strip(): r["id"] for r in csv.DictReader(fh) if r.get("nutrient_nbr")}
    id_to_col = {nbr_to_id[n]: col for col, n in NUTRIENT_NBR.items() if n in nbr_to_id}

    with open(args.foods, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames
        foods = list(reader)
    wanted = {r["fdc_id"] for r in foods if r["fdc_id"]}
    values: dict[str, dict[str, float]] = {}
    with open(fdc / "food_nutrient.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["fdc_id"] in wanted and r["nutrient_id"] in id_to_col:
                values.setdefault(r["fdc_id"], {})[id_to_col[r["nutrient_id"]]] = float(r["amount"])

    provenance = []
    for row in foods:
        if not row["fdc_id"]:
            continue
        got = values.get(row["fdc_id"], {})
        missing = [c for c in NUTRIENT_NBR if c not in got]
        for col, v in got.items():
            row[col] = f"{v:g}"
        for col in NUTRIENT_NBR:
            provenance.append({"food_id": row["food_id"], "nutrient_id": col, "fdc_id": row["fdc_id"],
                               "source": "USDA FDC SR Legacy 2018-04" if col in got else
                               "imputed (not reported in SR Legacy; approximate value kept)"})
        print(f"{row['food_id']} {row['name']}: updated {len(got)} nutrients"
              + (f"; missing {missing} (kept old values)" if missing else ""))

    with open(args.foods, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(foods)
    prov_path = Path(args.foods).with_name("nutrient_provenance.csv")
    with open(prov_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["food_id", "nutrient_id", "fdc_id", "source"])
        w.writeheader()
        w.writerows(provenance)
    imputed = [p for p in provenance if p["source"].startswith("imputed")]
    print(f"{len(provenance) - len(imputed)} USDA values, {len(imputed)} imputed -> {prov_path.name}")


if __name__ == "__main__":
    main()
