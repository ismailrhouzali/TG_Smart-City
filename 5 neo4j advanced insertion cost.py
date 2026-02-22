"""
5 neo4j advanced insertion cost.py
====================================
PARTIE 1 — Définition des métriques et des profils de routage
PARTIE 2 — Calcul des coûts depuis ca_his_raw_2021.h5 (congestion par jour)
PARTIE 3 — Insertion des coûts dans Neo4j

Coûts insérés sur chaque arc ROUTE :
  cost_time_trafic        C3 = distance / speed_annuelle (min)
  cost_congestion_lun/mar/mer/jeu/ven/sam/dim   C4 par jour
  cost_congestion_avg     C4_avg = moyenne des 7 jours

  Composites (C1=cost_dist, C3=cost_time_trafic normalisé, C4=cost_congestion_avg) :
  cost_rapide             🚗 0.10×C1 + 0.70×C3 + 0.20×C4_avg
  cost_eco                ⛽ 0.60×C1 + 0.20×C3 + 0.20×C4_avg
  cost_fluide             🌊 0.10×C1 + 0.20×C3 + 0.70×C4_avg
  cost_equilibre          ⚖️  0.33×C1 + 0.33×C3 + 0.34×C4_avg

10 profils de routage disponibles :
  fastest / rapide / comfortable / reliable / safe /
  eco / fluide / equilibre / economical / robuste
"""

import h5py
import math
import numpy as np
import pandas as pd
from neo4j import GraphDatabase
from tqdm import tqdm
from datetime import datetime, timedelta

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"
H5_PATH        = "/root/Desktop/TG/data/ca_his_raw_2021.h5"
EDGES_PATH     = "/root/Desktop/TG/data/edges.csv"
BATCH_SIZE     = 500
STEPS_PER_DAY  = 288                    # 24h × 12 pas de 5 min
START_DATE     = datetime(2021, 1, 1)   # Vendredi (weekday=4)
# Composites utilisent cost_congestion_avg (moyenne 7 jours) — pas un jour unique

DAY_NAMES_FR  = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
DAYS          = {i: d for i, d in enumerate(DAY_NAMES_FR)}

# ══════════════════════════════════════════════════════════════════════════════
#  PARTIE 1 — MÉTRIQUES ET PROFILS DE ROUTAGE
# ══════════════════════════════════════════════════════════════════════════════

# Métriques à MINIMISER (inversées dans le score)
MINIMIZE = {
    "total_time_min", "avg_congestion", "max_congestion",
    "danger_score", "worst_case_time_min", "energy_cost", "total_distance_km"
}

# Bornes de normalisation (min, max)
NORM_RANGES = {
    "total_distance_km"  : (0, 100),
    "total_time_min"     : (0, 120),
    "avg_congestion"     : (0, 1),
    "max_congestion"     : (0, 1),
    "reliability"        : (0, 1),
    "comfort"            : (0, 1),
    "simplicity"         : (0, 1),
    "danger_score"       : (0, 1),
    "worst_case_time_min": (0, 180),
    "robustness"         : (0, 10),
    "energy_cost"        : (0, 20),
    "num_transitions"    : (0, 50),
}

# ── 10 Profils de routage ────────────────────────────────────────────────────
ROUTING_PROFILES = {
    # ─ DeepSeek originals ─────────────────────────────────────────────────
    "fastest": {            # 🚗 Le plus rapide (temps pur)
        "total_time_min"    : 0.70,
        "avg_congestion"    : 0.20,
        "reliability"       : 0.10,
    },
    "reliable": {           # 🔒 Fiable, prévisible
        "reliability"        : 0.50,
        "total_time_min"     : 0.30,
        "worst_case_time_min": 0.20,
    },
    "comfortable": {        # 🛋️ Conduite fluide, peu de virages
        "comfort"           : 0.40,
        "total_time_min"    : 0.30,
        "avg_congestion"    : 0.20,
        "simplicity"        : 0.10,
    },
    "safe": {               # 🛡️ Le plus sûr
        "danger_score"      : 0.40,
        "reliability"       : 0.30,
        "robustness"        : 0.20,
        "total_time_min"    : 0.10,
    },
    "economical": {         # 💡 Économique énergie (VT-Micro)
        "energy_cost"       : 0.40,
        "total_distance_km" : 0.30,
        "total_time_min"    : 0.20,
        "comfort"           : 0.10,
    },
    # ─ Nos profils ────────────────────────────────────────────────────────
    "rapide": {             # 🚀 Rapide avec un peu de confort
        "total_time_min"    : 0.55,
        "avg_congestion"    : 0.25,
        "comfort"           : 0.20,
    },
    "eco": {                # ⛽ Min distance/carburant
        "total_distance_km" : 0.50,
        "energy_cost"       : 0.30,
        "avg_congestion"    : 0.20,
    },
    "fluide": {             # 🌊 Éviter la congestion avant tout
        "avg_congestion"    : 0.50,
        "max_congestion"    : 0.20,
        "comfort"           : 0.20,
        "total_time_min"    : 0.10,
    },
    "equilibre": {          # ⚖️ Équilibré multi-critères
        "total_time_min"    : 0.33,
        "avg_congestion"    : 0.33,
        "total_distance_km" : 0.34,
    },
    "robuste": {            # 🔗 Réseau bien connecté, alternatives max
        "robustness"        : 0.50,
        "reliability"       : 0.30,
        "danger_score"      : 0.20,
    },
}


def calculate_route_metrics(path_nodes, graph_data, day="ven"):
    """
    Calcule les 11 métriques d'un chemin.
    path_nodes  : [idx_A, idx_B, ...]
    graph_data  : {'nodes': {idx: {...}}, 'edges': {(u,v): {...}}}
    day         : jour de la semaine pour la congestion
    """
    n = len(path_nodes)
    if n < 2:
        return {}

    cong_key  = f"congestion_{day}"
    node_data = [graph_data["nodes"].get(nd, {}) for nd in path_nodes]

    speeds      = [nd.get("avg_speed", 60.0) for nd in node_data]
    stds        = [nd.get("std_speed", 10.0) for nd in node_data]
    congestions = [nd.get(cong_key, nd.get("avg_congestion", 0.1)) for nd in node_data]
    degrees     = [nd.get("degree", 2) for nd in node_data]
    lats        = [nd.get("lat") for nd in node_data]
    lngs        = [nd.get("lng") for nd in node_data]
    have_coords = all(x is not None for x in lats + lngs)

    distances_km, durations_min = [], []
    for i in range(n - 1):
        e = graph_data["edges"].get((path_nodes[i], path_nodes[i+1]), {})
        distances_km.append(e.get("distance_km", 0.0))
        durations_min.append(e.get("cost_time_trafic", e.get("duration_min", 0.0)))

    metrics = {}
    metrics["total_distance_km"] = sum(distances_km)
    metrics["total_time_min"]    = sum(durations_min)
    metrics["avg_congestion"]    = float(np.mean(congestions))
    metrics["max_congestion"]    = float(np.max(congestions))
    metrics["num_transitions"]   = n - 1

    # Fiabilité
    mean_speed = np.mean(speeds)
    mean_std   = np.mean(stds)
    cv = mean_std / mean_speed if mean_speed > 0 else 0
    metrics["reliability"] = float(1 / (1 + cv))

    # Confort (gradient de vitesse / km)
    gradients = []
    for i in range(n - 1):
        dv = abs(speeds[i+1] - speeds[i])
        dk = distances_km[i] if distances_km[i] > 0 else 0.001
        gradients.append(dv / dk)
    metrics["comfort"] = float(1 / (1 + np.mean(gradients) / 10.0))

    # Simplicité (angles de virage)
    def bearing(la1, lo1, la2, lo2):
        la1, la2 = math.radians(la1), math.radians(la2)
        dl = math.radians(lo2 - lo1)
        x = math.sin(dl) * math.cos(la2)
        y = math.cos(la1)*math.sin(la2) - math.sin(la1)*math.cos(la2)*math.cos(dl)
        return (math.degrees(math.atan2(x, y)) + 360) % 360

    if have_coords:
        angles = []
        for i in range(1, n - 1):
            try:
                b1 = bearing(lats[i-1], lngs[i-1], lats[i], lngs[i])
                b2 = bearing(lats[i],   lngs[i],   lats[i+1], lngs[i+1])
                a  = abs(b2 - b1)
                angles.append(360 - a if a > 180 else a)
            except:
                angles.append(0)
        sharp = sum(1 for a in angles if a > 30)
        complexity = (n-1) + 0.5*sharp + 0.01*sum(angles)
    else:
        dirs = [nd.get("direction") for nd in node_data]
        nc   = sum(1 for i in range(n-1) if dirs[i] != dirs[i+1]) if all(dirs) else 0
        complexity = (n-1) + nc
    metrics["simplicity"] = float(1 / (1 + complexity))

    # Dangerosité (HSM-inspired)
    norm_cong  = metrics["avg_congestion"]
    norm_var   = mean_std / 120.0
    norm_speed = mean_speed / 120.0
    metrics["danger_score"] = float(0.40*norm_cong + 0.30*norm_var + 0.30*norm_speed)

    # Temps pire cas (95e percentile)
    wt = 0.0
    for i in range(n - 1):
        wv = max(speeds[i+1] - 2*stds[i+1], 10.0)
        wt += (distances_km[i] / wv) * 60 if wv > 0 else 999
    metrics["worst_case_time_min"] = float(wt)

    # Robustesse
    metrics["robustness"] = float(np.mean(degrees))

    # Coût énergétique (VT-Micro simplifié, litres)
    a, b, c = 0.05, 2.5, 0.0003
    fuel = sum(
        distances_km[i] * (a + b/max(speeds[i], 1) + c*speeds[i]**2) / 100
        for i in range(n-1)
    )
    penalty = 0.01 * sum(abs(speeds[i+1]-speeds[i]) for i in range(n-1))
    metrics["energy_cost"] = float(fuel + penalty)

    return metrics


def calculate_weighted_score(metrics, weights=None, profile="equilibre"):
    """
    Score pondéré ∈ [0,1]  (plus élevé = meilleur itinéraire).
    weights override le profil si fourni.
    """
    if weights is None:
        weights = ROUTING_PROFILES.get(profile, ROUTING_PROFILES["equilibre"])
    score = 0.0
    for metric, weight in weights.items():
        if metric not in metrics:
            continue
        mn, mx   = NORM_RANGES.get(metric, (0, 1))
        norm_val = max(0.0, min(1.0, (metrics[metric] - mn) / (mx - mn + 1e-9)))
        score   += weight * ((1 - norm_val) if metric in MINIMIZE else norm_val)
    return round(score, 6)


# ══════════════════════════════════════════════════════════════════════════════
#  PARTIE 2 — CALCUL DES COÛTS DEPUIS LE H5
# ══════════════════════════════════════════════════════════════════════════════

print("=" * 60)
print("  PARTIE 2 — Calcul des coûts depuis ca_his_raw_2021.h5")
print("=" * 60)

print("\n📂 Structure H5...")
with h5py.File(H5_PATH, "r") as f:
    def show(name, obj):
        if hasattr(obj, 'shape'):
            print(f"   DATASET: {name} | shape={obj.shape} | dtype={obj.dtype}")
        else:
            print(f"   GROUP  : {name}")
    f.visititems(show)

# ─── LECTURE CHUNKÉE (1 jour = 288 steps = ~20 MB) ────────────────────────────
# Charge 1 jour à la fois pour éviter l'OOM (fichier = 7.24 GB)
print("\n📊 Calcul des moyennes par jour (lecture chunkée)...")

with h5py.File(H5_PATH, "r") as f:
    ds   = f["t/block0_values"]           # shape (105120, 8600) float64
    T, N = ds.shape

start_wd = START_DATE.weekday()           # 4 = vendredi
n_days   = T // STEPS_PER_DAY
print(f"   T={T} steps | N={N} capteurs | {n_days} jours | STEPS_PER_DAY={STEPS_PER_DAY}")
print(f"   Mémoire par lecture : ~{STEPS_PER_DAY * N * 8 / 1e6:.1f} MB")

# Accumulateurs NaN-safe : somme des valeurs valides + compteur de valides
speed_sum  = {d: np.zeros(N, dtype=np.float64) for d in DAY_NAMES_FR}
speed_cnt  = {d: np.zeros(N, dtype=np.float64) for d in DAY_NAMES_FR}
speed_max_global = 0.0
SPEED_SENTINEL   = 120.0   # mph : valeurs > 120 = sentinelles invalides (ex. 999)
SPEED_MAX_REF    = 80.0    # mph : vitesse libre californienne (55-80 mph)

with h5py.File(H5_PATH, "r") as f:
    ds = f["t/block0_values"]
    for day_i in tqdm(range(n_days), desc="Jours", unit="jour"):
        t_start = day_i * STEPS_PER_DAY
        t_end   = t_start + STEPS_PER_DAY
        chunk   = ds[t_start:t_end, :]                   # (288, N)
        d_name  = DAYS[(day_i + start_wd) % 7]
        # Masque valide : non-NaN ET < seuil sentinelle (999 = invalide PeMS)
        valid   = (~np.isnan(chunk)) & (chunk <= SPEED_SENTINEL) & (chunk >= 0)
        chunk_clean = np.where(valid, chunk, 0.0)
        speed_sum[d_name] += chunk_clean.sum(axis=0)     # somme valides
        speed_cnt[d_name] += valid.sum(axis=0)           # nb valides/capteur
        mx = chunk_clean[valid].max() if valid.any() else 0.0
        if mx > speed_max_global:
            speed_max_global = mx

# Moyennes par capteur et par jour (NaN-safe)
speed_by_day = {
    d: np.where(speed_cnt[d] > 0, speed_sum[d] / np.maximum(speed_cnt[d], 1), 0.0)
    for d in DAY_NAMES_FR
}
total_sum  = sum(speed_sum.values())
total_cnt  = sum(speed_cnt.values())
speed_mean = np.where(total_cnt > 0, total_sum / np.maximum(total_cnt, 1), 0.0)
# Référence de congestion : vitesse libre (free-flow) californienne
# Plus stable que le max observé ; permet une congestion ∈ [0, 1] cohérente
speed_max  = SPEED_MAX_REF   # 80 mph = ~130 km/h

print(f"\n   Speed max : {speed_max:.1f} | Moy annuelle : {speed_mean.mean():.1f}")
for d_name in DAY_NAMES_FR:
    sm   = speed_by_day[d_name].mean()
    cong = np.clip(1.0 - speed_by_day[d_name] / speed_max, 0, 1).mean()
    print(f"   {d_name.upper()} : speed_moy={sm:.1f} | congestion_moy={cong:.4f}")

# Congestion par capteur par jour ∈ [0, 1]
occ_by_day = {
    d: np.clip(1.0 - speed_by_day[d] / speed_max, 0, 1)
    for d in DAY_NAMES_FR
}

print("\n📂 Chargement edges.csv...")
edges = pd.read_csv(EDGES_PATH)
print(f"   {len(edges)} arcs\n")

def safe_speed(arr, i):
    return max(float(arr[min(i, len(arr)-1)]), 1.0)

# C3 — temps trafic annuel
print("⚙️  C3 : temps avec trafic...")
edges["cost_time_trafic"] = edges.apply(
    lambda r: round(r["distance_km"]
        / ((safe_speed(speed_mean, int(r["from_id"])) + safe_speed(speed_mean, int(r["to_id"]))) / 2)
        * 60, 4), axis=1)

# C4 — congestion par jour
print("⚙️  C4 : congestion par jour...")
for d_name in DAY_NAMES_FR:
    arr = occ_by_day[d_name]
    edges[f"cost_congestion_{d_name}"] = edges.apply(
        lambda r, a=arr: round(float((a[int(r["from_id"])] + a[int(r["to_id"])]) / 2), 6), axis=1)

# ── Normalisation ────────────────────────────────────────────────────────────
# C1 = cost_dist (déjà calculé et stocké, normalisé ∈ [0,1])
# C3 = cost_time_trafic (en minutes → à normaliser)
# C4 = cost_congestion_avg (moyenne 7 jours, déjà ∈ [0,1])
def norm(col):
    mn, mx = col.min(), col.max()
    return (col - mn) / (mx - mn + 1e-9)

c1_n  = norm(edges["distance_km"])          # = cost_dist (même résultat)
c3_n  = norm(edges["cost_time_trafic"])     # C3 normalisé ∈ [0,1]

# C4_avg : moyenne des 7 congestions journalières
cong_cols = [f"cost_congestion_{d}" for d in DAY_NAMES_FR]
edges["cost_congestion_avg"] = edges[cong_cols].mean(axis=1)
c4_avg = norm(edges["cost_congestion_avg"])

# ── Composites ───────────────────────────────────────────────────────────────
# Formules utilisant C1=cost_dist, C3=cost_time_trafic norm., C4=cost_congestion_avg
edges["cost_rapide"]    = (0.10*c1_n + 0.70*c3_n + 0.20*c4_avg).round(6)
edges["cost_eco"]       = (0.60*c1_n + 0.20*c3_n + 0.20*c4_avg).round(6)
edges["cost_fluide"]    = (0.10*c1_n + 0.20*c3_n + 0.70*c4_avg).round(6)
edges["cost_equilibre"] = (0.33*c1_n + 0.33*c3_n + 0.34*c4_avg).round(6)

print("\n   Aperçu coûts :")
for col in ["cost_time_trafic", "cost_congestion_avg",
            "cost_rapide", "cost_eco", "cost_fluide", "cost_equilibre"]:
    print(f"   {col:35s} moy={edges[col].mean():.4f}")

# ══════════════════════════════════════════════════════════════════════════════
#  PARTIE 3 — INSERTION DANS NEO4J
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*60}")
print("  PARTIE 3 — Insertion dans Neo4j")
print(f"{'='*60}\n")

print("🔌 Connexion Neo4j...")
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("   ✅ Connecté !\n")

cols = (["from_id", "to_id", "cost_time_trafic"]
      + [f"cost_congestion_{d}" for d in DAY_NAMES_FR]
      + ["cost_congestion_avg"]
      + ["cost_rapide", "cost_eco", "cost_fluide", "cost_equilibre"])
records = edges[cols].to_dict("records")

def update_edges(tx, batch):
    tx.run("""
        UNWIND $batch AS row
        MATCH (a:Sensor {index: row.from_id})-[r:ROUTE]->(b:Sensor {index: row.to_id})
        SET r.cost_time_trafic     = row.cost_time_trafic,
            r.cost_congestion_lun  = row.cost_congestion_lun,
            r.cost_congestion_mar  = row.cost_congestion_mar,
            r.cost_congestion_mer  = row.cost_congestion_mer,
            r.cost_congestion_jeu  = row.cost_congestion_jeu,
            r.cost_congestion_ven  = row.cost_congestion_ven,
            r.cost_congestion_sam  = row.cost_congestion_sam,
            r.cost_congestion_dim  = row.cost_congestion_dim,
            r.cost_congestion_avg  = row.cost_congestion_avg,
            r.cost_rapide          = row.cost_rapide,
            r.cost_eco             = row.cost_eco,
            r.cost_fluide          = row.cost_fluide,
            r.cost_equilibre       = row.cost_equilibre
    """, batch=batch)

print(f"🔄 Mise à jour Neo4j (batch={BATCH_SIZE})...")
with driver.session() as session:
    for i in tqdm(range(0, len(records), BATCH_SIZE), desc="MAJ arcs", unit="batch"):
        session.execute_write(update_edges, records[i: i+BATCH_SIZE])

with driver.session() as session:
    n = session.run("""
        MATCH ()-[r:ROUTE]->() WHERE r.cost_rapide IS NOT NULL
        RETURN count(r) AS n
    """).single()["n"]

print(f"\n✅ {n} arcs mis à jour !\n")
print("📋 Propriétés sur chaque arc ROUTE :")
print("   distance_km              → C1 statique (km)")
print("   duration_min             → C2 statique (min, vitesse libre)")
print("   cost_dist                → C1 normalisé ∈ [0,1]")
print("   cost_time_libre          → C2 normalisé ∈ [0,1]")
print("   weight_adj               → 1/distance (structurel)")
print("   cost_time_trafic         → C3 : temps réel trafic (min)")
print("   cost_congestion_lun→dim  → C4 par jour ∈ [0,1]")
print("   cost_congestion_avg      → C4_avg : moyenne 7 jours ∈ [0,1]")
print("   cost_rapide              → 0.10×C1 + 0.70×C3 + 0.20×C4_avg")
print("   cost_eco                 → 0.60×C1 + 0.20×C3 + 0.20×C4_avg")
print("   cost_fluide              → 0.10×C1 + 0.20×C3 + 0.70×C4_avg")
print("   cost_equilibre           → 0.33×C1 + 0.33×C3 + 0.34×C4_avg")
print(f"\n🧭 10 profils disponibles : {', '.join(ROUTING_PROFILES.keys())}")

driver.close()
