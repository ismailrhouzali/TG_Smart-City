"""
3 osrm distance.py
------------------
But : Pour chaque paire (i, j) où 0 < adj[i,j] < 1,
      calculer la vraie distance routière via OSRM.
      Résultat : fichier edges.csv  (from_id, to_id, distance_km, duration_min, weight_adj)
"""

import numpy as np
import pandas as pd
import requests
from tqdm import tqdm

# ─── CONFIG ───────────────────────────────────────────────────────────────────
OSRM_URL    = "http://localhost:5000"
ADJ_PATH    = "/root/Desktop/TG/data/ca_rn_adj.npy"
META_PATH   = "/root/Desktop/TG/data/ca_meta.csv"
OUTPUT_CSV  = "/root/Desktop/TG/data/edges.csv"
BATCH_SIZE  = 100   # nb de destinations par appel Table API

# ─── CHARGEMENT ───────────────────────────────────────────────────────────────
print("📂 Chargement des données...")
adj  = np.load(ADJ_PATH)
meta = pd.read_csv(META_PATH)
print(f"   Matrice adj : {adj.shape}")
print(f"   Meta CSV    : {meta.shape}")
print(f"   Colonnes    : {list(meta.columns)}\n")

# Coordonnées des capteurs (lon, lat pour OSRM)
LAT_COL = [c for c in meta.columns if 'lat' in c.lower()][0]
LON_COL = [c for c in meta.columns if 'lon' in c.lower() or 'lng' in c.lower()][0]
coords  = list(zip(meta[LON_COL].values, meta[LAT_COL].values))
print(f"   lat='{LAT_COL}' | lon='{LON_COL}' | ex: {coords[0]}\n")

# ─── PAIRES À CALCULER ────────────────────────────────────────────────────────
print("🔍 Paires connectées (0 < adj < 1)...")
rows_idx, cols_idx = np.where((adj > 0) & (adj < 1))
n_pairs = len(rows_idx)
print(f"   → {n_pairs} arcs à traiter\n")

# ─── FONCTION OSRM TABLE API ──────────────────────────────────────────────────
def osrm_table(src_coords, dst_coords):
    """Retourne (distances_km, durations_min) matrices via OSRM Table API."""
    all_c    = src_coords + dst_coords
    coord_str = ";".join(f"{lon},{lat}" for lon, lat in all_c)
    src_str  = ";".join(str(i) for i in range(len(src_coords)))
    dst_str  = ";".join(str(i) for i in range(len(src_coords), len(all_c)))

    url    = f"{OSRM_URL}/table/v1/driving/{coord_str}"
    params = {"sources": src_str, "destinations": dst_str,
              "annotations": "distance,duration"}
    resp   = requests.get(url, params=params, timeout=30).json()

    if resp.get("code") != "Ok":
        return None, None

    dist_km  = np.array(resp["distances"],  dtype=np.float32) / 1000.0
    dur_min  = np.array(resp["durations"],  dtype=np.float32) / 60.0
    return dist_km, dur_min

# ─── TRAITEMENT PAR BATCH ─────────────────────────────────────────────────────
print(f"🚀 Calcul OSRM (batch={BATCH_SIZE})...\n")

records    = []   # liste de dicts → CSV
errors     = 0
unique_src = np.unique(rows_idx)

with tqdm(total=n_pairs, desc="Arcs calculés", unit="arc") as pbar:
    for src in unique_src:
        dsts = cols_idx[rows_idx == src]

        for b in range(0, len(dsts), BATCH_SIZE):
            batch = dsts[b : b + BATCH_SIZE]
            src_c = [coords[src]]
            dst_c = [coords[d] for d in batch]

            try:
                dist_km, dur_min = osrm_table(src_c, dst_c)
                if dist_km is not None:
                    for k, dst in enumerate(batch):
                        records.append({
                            "from_id"     : int(src),
                            "to_id"       : int(dst),
                            "distance_km" : round(float(dist_km[0, k]), 4),
                            "duration_min": round(float(dur_min[0, k]), 4),
                            "weight_adj"  : round(float(adj[src, dst]), 6),
                        })
                else:
                    errors += len(batch)
            except Exception as e:
                errors += len(batch)

            pbar.update(len(batch))

# ─── SAUVEGARDE CSV ───────────────────────────────────────────────────────────
df = pd.DataFrame(records, columns=["from_id","to_id","distance_km","duration_min","weight_adj"])
df.to_csv(OUTPUT_CSV, index=False)

print(f"\n✅ Terminé ! {len(df)} arcs calculés | Erreurs : {errors}")
print(f"\n📊 Statistiques distances (km) :")
print(f"   Min    : {df['distance_km'].min():.2f} km")
print(f"   Max    : {df['distance_km'].max():.2f} km")
print(f"   Moy    : {df['distance_km'].mean():.2f} km")
print(f"   Médiane: {df['distance_km'].median():.2f} km")
print(f"\n💾 Fichier sauvegardé : {OUTPUT_CSV}")
print(f"   Lignes : {len(df)} | Colonnes : {list(df.columns)}")
print(f"\n   Aperçu :")
print(df.head(10).to_string(index=False))
