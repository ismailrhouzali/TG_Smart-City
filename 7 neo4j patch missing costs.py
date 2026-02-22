"""
7 neo4j patch missing costs.py
================================
Ajoute les 3 propriétés manquantes sur les arcs ROUTE :
  ❌ → ✅  cost_dist            C1 normalisé ∈ [0,1]
  ❌ → ✅  cost_time_libre      C2 normalisé ∈ [0,1]
  ❌ → ✅  cost_congestion_avg  moyenne des 7 jours C4 ∈ [0,1]

Corrige aussi les valeurs négatives détectées :
  ⚠️  distance_km      min = -0.0018  → clamp à 0
  ⚠️  cost_time_trafic min = -0.0960  → clamp à 0
"""

import numpy as np
import pandas as pd
from neo4j import GraphDatabase
from tqdm import tqdm

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"
BATCH_SIZE     = 500

DAY_NAMES_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]

# ══════════════════════════════════════════════════════════════════════════════
print("=" * 65)
print("  🔧  PATCH — Ajout des coûts manquants + correction anomalies")
print("=" * 65)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connecté à Neo4j\n")

# ─── ÉTAPE 1 : Lecture de tous les arcs ───────────────────────────────────────
print("📥 Lecture de tous les arcs ROUTE...")

cols_needed = (
    ["from_id", "to_id", "distance_km", "duration_min", "cost_time_trafic"]
    + [f"cost_congestion_{d}" for d in DAY_NAMES_FR]
)

with driver.session() as s:
    rows = s.run("""
        MATCH (a:Sensor)-[r:ROUTE]->(b:Sensor)
        RETURN
            a.index                    AS from_id,
            b.index                    AS to_id,
            r.distance_km              AS distance_km,
            r.duration_min             AS duration_min,
            r.cost_time_trafic         AS cost_time_trafic,
            r.cost_congestion_lun      AS cost_congestion_lun,
            r.cost_congestion_mar      AS cost_congestion_mar,
            r.cost_congestion_mer      AS cost_congestion_mer,
            r.cost_congestion_jeu      AS cost_congestion_jeu,
            r.cost_congestion_ven      AS cost_congestion_ven,
            r.cost_congestion_sam      AS cost_congestion_sam,
            r.cost_congestion_dim      AS cost_congestion_dim
    """).data()

df = pd.DataFrame(rows)
print(f"   {len(df):,} arcs chargés\n")

# ─── ÉTAPE 2 : Correction des valeurs négatives ────────────────────────────────
print("🔧 Correction des valeurs négatives...")

neg_dist = (df["distance_km"] < 0).sum()
neg_time = (df["cost_time_trafic"] < 0).sum()

if neg_dist > 0:
    print(f"   ⚠️  distance_km       : {neg_dist} valeurs négatives → clamp à 0.0")
    df["distance_km"] = df["distance_km"].clip(lower=0.0)

if neg_time > 0:
    print(f"   ⚠️  cost_time_trafic  : {neg_time} valeurs négatives → clamp à 0.0")
    df["cost_time_trafic"] = df["cost_time_trafic"].clip(lower=0.0)

if neg_dist == 0 and neg_time == 0:
    print("   ✅ Aucune valeur négative détectée")

print()

# ─── ÉTAPE 3 : Calcul des 3 propriétés manquantes ────────────────────────────
print("⚙️  Calcul des propriétés manquantes...\n")

# ── cost_dist (C1 normalisé) ──────────────────────────────────────────────────
d_min = df["distance_km"].min()
d_max = df["distance_km"].max()
df["cost_dist"] = ((df["distance_km"] - d_min) / (d_max - d_min + 1e-9)).round(6)
print(f"   ✅ cost_dist         : normalisé depuis distance_km "
      f"[{d_min:.4f}, {d_max:.4f}] → moy={df['cost_dist'].mean():.4f}")

# ── cost_time_libre (C2 normalisé) ───────────────────────────────────────────
t_min = df["duration_min"].min()
t_max = df["duration_min"].max()
df["cost_time_libre"] = ((df["duration_min"] - t_min) / (t_max - t_min + 1e-9)).round(6)
print(f"   ✅ cost_time_libre   : normalisé depuis duration_min  "
      f"[{t_min:.4f}, {t_max:.4f}] → moy={df['cost_time_libre'].mean():.4f}")

# ── cost_congestion_avg (C4 moyenne 7 jours) ─────────────────────────────────
cong_cols = [f"cost_congestion_{d}" for d in DAY_NAMES_FR]
df["cost_congestion_avg"] = df[cong_cols].mean(axis=1).round(6)
print(f"   ✅ cost_congestion_avg: moyenne({', '.join(DAY_NAMES_FR)}) "
      f"→ moy={df['cost_congestion_avg'].mean():.4f}")

print()

# ─── ÉTAPE 4 : Mise à jour Neo4j ─────────────────────────────────────────────
print(f"🔄 Mise à jour Neo4j (batch={BATCH_SIZE})...")

records = df[[
    "from_id", "to_id",
    "distance_km",          # corrigé (clamp négatif)
    "cost_time_trafic",     # corrigé (clamp négatif)
    "cost_dist",
    "cost_time_libre",
    "cost_congestion_avg",
]].to_dict("records")

def patch_edges(tx, batch):
    tx.run("""
        UNWIND $batch AS row
        MATCH (a:Sensor {index: row.from_id})-[r:ROUTE]->(b:Sensor {index: row.to_id})
        SET
            r.distance_km          = row.distance_km,
            r.cost_time_trafic     = row.cost_time_trafic,
            r.cost_dist            = row.cost_dist,
            r.cost_time_libre      = row.cost_time_libre,
            r.cost_congestion_avg  = row.cost_congestion_avg
    """, batch=batch)

with driver.session() as session:
    for i in tqdm(range(0, len(records), BATCH_SIZE), desc="Patch arcs", unit="batch"):
        session.execute_write(patch_edges, records[i: i + BATCH_SIZE])

# ─── ÉTAPE 5 : Vérification finale ────────────────────────────────────────────
print("\n🔍 Vérification finale...\n")

PROPS_CHECK = [
    "cost_dist", "cost_time_libre", "cost_congestion_avg",
    "distance_km", "cost_time_trafic",
]

with driver.session() as s:
    for prop in PROPS_CHECK:
        res = s.run(f"""
            MATCH ()-[r:ROUTE]->()
            WHERE r.`{prop}` IS NOT NULL
            RETURN
                count(r)      AS cnt,
                min(r.`{prop}`)  AS mn,
                max(r.`{prop}`)  AS mx,
                avg(r.`{prop}`)  AS av
        """).single()

        null_res = s.run(f"""
            MATCH ()-[r:ROUTE]->()
            WHERE r.`{prop}` IS NULL
            RETURN count(r) AS nulls
        """).single()

        cnt   = res["cnt"]
        nulls = null_res["nulls"]
        mn    = round(res["mn"], 6) if res["mn"] is not None else "—"
        mx    = round(res["mx"], 6) if res["mx"] is not None else "—"
        av    = round(res["av"], 6) if res["av"] is not None else "—"

        status = "✅" if nulls == 0 and (mn == "—" or mn >= 0) else "⚠️ "
        print(f"   {status}  {prop:<25s}  cnt={cnt:,}  nuls={nulls}  "
              f"min={mn}  max={mx}  moy={av}")

# ─── Résumé final ─────────────────────────────────────────────────────────────
print(f"\n{'=' * 65}")
print("  ✅  PATCH TERMINÉ — Propriétés des arcs ROUTE (18/18)")
print("=" * 65)
print("""
  ── FICHIER 4 (base) ─────────────────────────────────────────
  ✅  distance_km          C1 brut (km)
  ✅  duration_min         C2 brut (min, vitesse libre 90 km/h)
  ✅  cost_dist            C1 normalisé ∈ [0,1]   ← AJOUTÉ
  ✅  cost_time_libre      C2 normalisé ∈ [0,1]   ← AJOUTÉ
  ✅  weight_adj           1 / distance_km

  ── FICHIER 5 (trafic HDF5) ──────────────────────────────────
  ✅  cost_time_trafic     C3 (min, vitesse réelle)
  ✅  cost_congestion_lun  C4 lundi    ∈ [0,1]
  ✅  cost_congestion_mar  C4 mardi    ∈ [0,1]
  ✅  cost_congestion_mer  C4 mercredi ∈ [0,1]
  ✅  cost_congestion_jeu  C4 jeudi    ∈ [0,1]
  ✅  cost_congestion_ven  C4 vendredi ∈ [0,1]
  ✅  cost_congestion_sam  C4 samedi   ∈ [0,1]
  ✅  cost_congestion_dim  C4 dimanche ∈ [0,1]
  ✅  cost_congestion_avg  C4_avg (moy 7 jours)   ← AJOUTÉ

  ── COMPOSITES ───────────────────────────────────────────────
  ✅  cost_rapide          🚗 0.10×C1 + 0.70×C3 + 0.20×C4_ven
  ✅  cost_eco             ⛽ 0.60×C1 + 0.20×C3 + 0.20×C4_ven
  ✅  cost_fluide          🌊 0.10×C1 + 0.20×C3 + 0.70×C4_ven
  ✅  cost_equilibre       ⚖️  0.33×C1 + 0.33×C3 + 0.34×C4_ven
""")

driver.close()
