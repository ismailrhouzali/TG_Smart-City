"""
6 neo4j inspect arcs.py
========================
Affiche toutes les propriétés des arcs ROUTE dans Neo4j :
  - Liste des propriétés existantes
  - Statistiques (min / max / moyenne / nb nuls)
  - Comparaison avec la spec finale (ce qui manque)
"""

from neo4j import GraphDatabase
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"
SAMPLE_SIZE    = 5          # nb d'arcs à afficher en détail
BATCH_STATS    = 5000       # nb d'arcs pour les stats globales

# ── Spec finale attendue ───────────────────────────────────────────────────────
EXPECTED_PROPS = {
    # ── Fichier 4 (base) ──────────────────────────────────────────────────
    "distance_km"          : "C1 brut (km)",
    "duration_min"         : "C2 brut (min, vitesse libre 90 km/h)",
    "cost_dist"            : "C1 normalisé ∈ [0,1]",
    "cost_time_libre"      : "C2 normalisé ∈ [0,1]",
    "weight_adj"           : "1 / distance_km (graphe structurel)",
    # ── Fichier 5 (avancé trafic) ─────────────────────────────────────────
    "cost_time_trafic"     : "C3 - temps réel trafic (min, km/h corrigé)",
    "cost_congestion_lun"  : "C4 lundi    ∈ [0,1]",
    "cost_congestion_mar"  : "C4 mardi    ∈ [0,1]",
    "cost_congestion_mer"  : "C4 mercredi ∈ [0,1]",
    "cost_congestion_jeu"  : "C4 jeudi    ∈ [0,1]",
    "cost_congestion_ven"  : "C4 vendredi ∈ [0,1]",
    "cost_congestion_sam"  : "C4 samedi   ∈ [0,1]",
    "cost_congestion_dim"  : "C4 dimanche ∈ [0,1]",
    "cost_congestion_avg"  : "C4_avg - moyenne 7 jours ∈ [0,1]",
    # ── Composites ────────────────────────────────────────────────────────
    "cost_rapide"          : "0.10×C1 + 0.70×C3 + 0.20×C4_ven",
    "cost_eco"             : "0.60×C1 + 0.20×C3 + 0.20×C4_ven",
    "cost_fluide"          : "0.10×C1 + 0.20×C3 + 0.70×C4_ven",
    "cost_equilibre"       : "0.33×C1 + 0.33×C3 + 0.34×C4_ven",
}

# ══════════════════════════════════════════════════════════════════════════════
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()

print("=" * 70)
print("  🔍  INSPECTION DES ARCS ROUTE — NEO4J")
print("=" * 70)

# ─── 1. Comptage global ───────────────────────────────────────────────────────
with driver.session() as s:
    total = s.run("MATCH ()-[r:ROUTE]->() RETURN count(r) AS n").single()["n"]

print(f"\n📊 Nombre total d'arcs ROUTE : {total:,}\n")

# ─── 2. Propriétés existantes (depuis un échantillon) ─────────────────────────
print("─" * 70)
print("  📋  PROPRIÉTÉS EXISTANTES SUR LES ARCS")
print("─" * 70)

with driver.session() as s:
    rows = s.run(f"""
        MATCH ()-[r:ROUTE]->()
        RETURN properties(r) AS props
        LIMIT {BATCH_STATS}
    """).data()

all_props = set()
for row in rows:
    all_props.update(row["props"].keys())

all_props_sorted = sorted(all_props)
print(f"\n  Propriétés trouvées ({len(all_props_sorted)}) :\n")
for p in all_props_sorted:
    tag = "✅" if p in EXPECTED_PROPS else "🔷"
    desc = EXPECTED_PROPS.get(p, "(propriété hors spec)")
    print(f"  {tag}  {p:<30s}  {desc}")

# ─── 3. Stats par propriété ───────────────────────────────────────────────────
print(f"\n{'─' * 70}")
print("  📈  STATISTIQUES PAR PROPRIÉTÉ (sur {BATCH_STATS:,} arcs)")
print(f"{'─' * 70}\n")

numeric_props = [p for p in all_props_sorted]
stats_data = []

with driver.session() as s:
    for prop in numeric_props:
        res = s.run(f"""
            MATCH ()-[r:ROUTE]->()
            WHERE r.`{prop}` IS NOT NULL
            RETURN
                count(r.`{prop}`)  AS cnt,
                min(r.`{prop}`)    AS mn,
                max(r.`{prop}`)    AS mx,
                avg(r.`{prop}`)    AS av
            LIMIT {BATCH_STATS}
        """).single()

        null_res = s.run(f"""
            MATCH ()-[r:ROUTE]->()
            WHERE r.`{prop}` IS NULL
            RETURN count(r) AS nulls
            LIMIT {BATCH_STATS}
        """).single()

        cnt   = res["cnt"]   if res else 0
        nulls = null_res["nulls"] if null_res else 0
        mn    = round(res["mn"],  4) if res and res["mn"]  is not None else "—"
        mx    = round(res["mx"],  4) if res and res["mx"]  is not None else "—"
        av    = round(res["av"],  4) if res and res["av"]  is not None else "—"

        stats_data.append({
            "Propriété" : prop,
            "Présents"  : cnt,
            "Nuls"      : nulls,
            "Min"       : mn,
            "Max"       : mx,
            "Moyenne"   : av,
        })

df = pd.DataFrame(stats_data)
print(df.to_string(index=False))

# ─── 4. Affichage détaillé de N arcs ──────────────────────────────────────────
print(f"\n{'─' * 70}")
print(f"  🔎  DÉTAIL DE {SAMPLE_SIZE} ARCS (échantillon)")
print(f"{'─' * 70}\n")

with driver.session() as s:
    sample = s.run(f"""
        MATCH (a:Sensor)-[r:ROUTE]->(b:Sensor)
        RETURN
            a.index   AS from_id,
            b.index   AS to_id,
            properties(r) AS props
        LIMIT {SAMPLE_SIZE}
    """).data()

for i, row in enumerate(sample, 1):
    print(f"  Arc #{i}  ({row['from_id']} → {row['to_id']})")
    for k, v in sorted(row["props"].items()):
        val = f"{v:.6f}" if isinstance(v, float) else str(v)
        print(f"    {k:<30s} = {val}")
    print()

# ─── 5. Bilan : ce qui manque ─────────────────────────────────────────────────
print("─" * 70)
print("  🧩  BILAN — PROPRIÉTÉS MANQUANTES (vs spec finale)")
print("─" * 70 + "\n")

missing = [p for p in EXPECTED_PROPS if p not in all_props]
present = [p for p in EXPECTED_PROPS if p in all_props]

print(f"  ✅  Présentes ({len(present)}/{len(EXPECTED_PROPS)}) :")
for p in present:
    print(f"       ✅  {p}")

print(f"\n  ❌  Manquantes ({len(missing)}/{len(EXPECTED_PROPS)}) :")
for p in missing:
    print(f"       ❌  {p:<30s}  ← {EXPECTED_PROPS[p]}")

print(f"\n{'=' * 70}")
if missing:
    print(f"  ⚠️   {len(missing)} propriétés à ajouter → relancer le script 4 ou 5")
else:
    print("  🎉  TOUS LES COÛTS SONT PRÉSENTS — graphe prêt !")
print("=" * 70 + "\n")

driver.close()
