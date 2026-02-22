"""
7b neo4j patch composites.py
==============================
Recalcule les 4 coûts composites avec la NOUVELLE formule :
  cost_rapide    = 0.10*C1 + 0.70*C3 + 0.20*C4_avg
  cost_eco       = 0.60*C1 + 0.20*C3 + 0.20*C4_avg
  cost_fluide    = 0.10*C1 + 0.20*C3 + 0.70*C4_avg
  cost_equilibre = 0.33*C1 + 0.33*C3 + 0.34*C4_avg

Où :
  C1      = cost_dist           (normalisé ∈ [0,1], déjà stocké)
  C3      = cost_time_trafic    (normalisé ∈ [0,1])
  C4_avg  = cost_congestion_avg (moyenne 7 jours ∈ [0,1], déjà stocké)

Changement vs ancienne version : C4_ven → C4_avg (plus robuste)
"""

import numpy as np
import pandas as pd
from neo4j import GraphDatabase
from tqdm import tqdm

# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"
BATCH_SIZE     = 500

print("=" * 62)
print("  🔄  PATCH COMPOSITES — Nouvelle formule C4_avg")
print("=" * 62)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connecté à Neo4j\n")

# ─── Lecture des propriétés nécessaires ───────────────────────────────────────
print("📥 Lecture des arcs...")

with driver.session() as s:
    rows = s.run("""
        MATCH (a:Sensor)-[r:ROUTE]->(b:Sensor)
        RETURN
            a.index                   AS from_id,
            b.index                   AS to_id,
            r.distance_km             AS distance_km,
            r.cost_time_trafic        AS cost_time_trafic,
            r.cost_congestion_avg     AS cost_congestion_avg
    """).data()

df = pd.DataFrame(rows)
print(f"   {len(df):,} arcs chargés\n")

# ─── Normalisation ─────────────────────────────────────────────────────────────
def norm(col):
    mn, mx = col.min(), col.max()
    return (col - mn) / (mx - mn + 1e-9)

c1_n   = norm(df["distance_km"])           # C1 normalisé  ∈ [0,1]
c3_n   = norm(df["cost_time_trafic"])      # C3 normalisé  ∈ [0,1]
c4_avg = norm(df["cost_congestion_avg"])   # C4_avg normalisé ∈ [0,1]

# ─── Nouvelles formules ────────────────────────────────────────────────────────
print("⚙️  Calcul des composites (C4 = cost_congestion_avg)...\n")

df["cost_rapide"]    = (0.10*c1_n + 0.70*c3_n + 0.20*c4_avg).round(6)
df["cost_eco"]       = (0.60*c1_n + 0.20*c3_n + 0.20*c4_avg).round(6)
df["cost_fluide"]    = (0.10*c1_n + 0.20*c3_n + 0.70*c4_avg).round(6)
df["cost_equilibre"] = (0.33*c1_n + 0.33*c3_n + 0.34*c4_avg).round(6)

for col in ["cost_rapide", "cost_eco", "cost_fluide", "cost_equilibre"]:
    print(f"   {col:<20s}  min={df[col].min():.4f}  "
          f"max={df[col].max():.4f}  moy={df[col].mean():.4f}")

# ─── Mise à jour Neo4j ─────────────────────────────────────────────────────────
print(f"\n🔄 Mise à jour Neo4j (batch={BATCH_SIZE})...")

records = df[["from_id", "to_id",
              "cost_rapide", "cost_eco",
              "cost_fluide", "cost_equilibre"]].to_dict("records")

def patch_composites(tx, batch):
    tx.run("""
        UNWIND $batch AS row
        MATCH (a:Sensor {index: row.from_id})-[r:ROUTE]->(b:Sensor {index: row.to_id})
        SET r.cost_rapide    = row.cost_rapide,
            r.cost_eco       = row.cost_eco,
            r.cost_fluide    = row.cost_fluide,
            r.cost_equilibre = row.cost_equilibre
    """, batch=batch)

with driver.session() as session:
    for i in tqdm(range(0, len(records), BATCH_SIZE),
                  desc="Patch composites", unit="batch"):
        session.execute_write(patch_composites, records[i: i + BATCH_SIZE])

# ─── Vérification ──────────────────────────────────────────────────────────────
print("\n🔍 Vérification finale...\n")

with driver.session() as s:
    for prop in ["cost_rapide", "cost_eco", "cost_fluide", "cost_equilibre"]:
        res = s.run(f"""
            MATCH ()-[r:ROUTE]->()
            RETURN count(r) AS cnt,
                   min(r.`{prop}`) AS mn,
                   max(r.`{prop}`) AS mx,
                   avg(r.`{prop}`) AS av
        """).single()
        print(f"   ✅  {prop:<20s}  cnt={res['cnt']:,}  "
              f"min={round(res['mn'],4)}  max={round(res['mx'],4)}  "
              f"moy={round(res['av'],4)}")

print(f"\n{'=' * 62}")
print("  ✅  COMPOSITES RECALCULÉS avec C4 = cost_congestion_avg")
print("=" * 62)
print("""
  cost_rapide    = 0.10×C1 + 0.70×C3 + 0.20×C4_avg  🚗
  cost_eco       = 0.60×C1 + 0.20×C3 + 0.20×C4_avg  ⛽
  cost_fluide    = 0.10×C1 + 0.20×C3 + 0.70×C4_avg  🌊
  cost_equilibre = 0.33×C1 + 0.33×C3 + 0.34×C4_avg  ⚖️
""")

driver.close()
