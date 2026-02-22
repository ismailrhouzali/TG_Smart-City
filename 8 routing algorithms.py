"""
8 routing algorithms.py
========================
Étape 1 : Charger le graphe Neo4j → networkx.DiGraph (une seule fois)
Étape 2 : 300 paires (100 courtes <10km / 100 moyennes 10-50km / 100 longues >50km)
Étape 3 : 7 algorithmes de routage
Étape 4 : Agrégation + export CSV
Étape 5 : Analyse console (tabulate)
"""

import math, random, time, threading, itertools, warnings, os
import numpy as np
import pandas as pd
import networkx as nx
from neo4j import GraphDatabase
from tqdm import tqdm
from datetime import datetime

try:
    from tabulate import tabulate
except ImportError:
    os.system("pip install tabulate -q")
    from tabulate import tabulate

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════════
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"

SEED           = 42
N_PAIRS_EACH   = 10
BF_TIMEOUT     = 30       # secondes par paire Bellman-Ford
FW_BFS_SIZE    = 30      # nœuds sous-graphe Floyd-Warshall
YEN_K          = 3
PARETO_N       = 10       # chemins énumérés pour Pareto
MAX_DIST_KM    = 48.51
MAX_SPEED_KMPH = 128.75   # 80 mph

PROFILES     = ["cost_rapide", "cost_eco", "cost_fluide", "cost_equilibre"]
DAY_NAMES_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
OUT_DIR      = "/root/Desktop/TG/data"
PAIRS_CSV    = f"{OUT_DIR}/pairs.csv"
RAW_CSV      = f"{OUT_DIR}/task_a_results_raw.csv"
AGG_CSV      = f"{OUT_DIR}/task_a_results_agg.csv"

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def path_cost(G, path, w):
    return sum(G[u][v].get(w, 0) for u, v in zip(path, path[1:]))

def path_hops(path):
    return len(path) - 1

def run_timeout(fn, timeout):
    res, err = [None], [None]
    def target():
        try: res[0] = fn()
        except Exception as e: err[0] = e
    t = threading.Thread(target=target, daemon=True)
    t.start(); t.join(timeout)
    if t.is_alive(): return None, "timeout"
    if err[0]:       return None, str(err[0])[:30]
    return res[0], None

def astar_heuristic(G, target, weight):
    """
    NetworkX appelle heuristic(u, v) — 2 arguments obligatoires.
    La cible est déjà capturée dans la closure (target), donc on ignore v.
    Heuristique : haversine normalisée → admissible pour les coûts ∈ [0,1].
    """
    tlat = G.nodes[target].get("lat", 0)
    tlng = G.nodes[target].get("lng", 0)
    def h(u, v):                                        # ← 2 args requis par NetworkX
        d = haversine_km(G.nodes[u].get("lat", 0), G.nodes[u].get("lng", 0), tlat, tlng)
        return d / MAX_DIST_KM * 0.05   # conservateur ∈ [0,1]
    return h

def pareto_front(solutions):
    """Indices des solutions non-dominées (minimisation)."""
    n = len(solutions)
    dominated = [False] * n
    for i in range(n):
        for j in range(n):
            if i == j: continue
            s, r = solutions[i], solutions[j]
            if all(r[k] <= s[k] for k in range(len(s))) and any(r[k] < s[k] for k in range(len(s))):
                dominated[i] = True; break
    return [i for i in range(n) if not dominated[i]]

NAN = float("nan")
def nan_row(src, dst, cat, algo, profil, note=""):
    return dict(source_id=src, dest_id=dst, categorie=cat, algo=algo,
                profil=profil, temps_ms=NAN, cout_total=NAN,
                nb_hops=NAN, succes=False, notes=note)

# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 1 — CHARGEMENT NEO4J → NETWORKX
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 62)
print("  ÉTAPE 1 — Chargement Neo4j → NetworkX DiGraph")
print("=" * 62)

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("✅ Connecté\n")

G = nx.DiGraph()

print("📥 Nœuds...")
with driver.session() as s:
    for n in s.run("MATCH (n:Sensor) RETURN n.index AS i, n.lat AS lat, n.lng AS lng").data():
        G.add_node(int(n["i"]), lat=float(n["lat"] or 0), lng=float(n["lng"] or 0))
print(f"   {G.number_of_nodes():,} nœuds")

print("📥 Arcs (toutes propriétés)...")
EDGE_PROPS = [
    "distance_km","duration_min","cost_dist","cost_time_libre","weight_adj",
    "cost_time_trafic",
    "cost_congestion_lun","cost_congestion_mar","cost_congestion_mer",
    "cost_congestion_jeu","cost_congestion_ven","cost_congestion_sam",
    "cost_congestion_dim","cost_congestion_avg",
    "cost_rapide","cost_eco","cost_fluide","cost_equilibre",
]
cypher_props = ", ".join(f"r.{p} AS {p}" for p in EDGE_PROPS)
with driver.session() as s:
    ed = s.run(f"MATCH (a:Sensor)-[r:ROUTE]->(b:Sensor) RETURN a.index AS src, b.index AS dst, {cypher_props}").data()

for e in ed:
    src, dst = int(e["src"]), int(e["dst"])
    props = {k: float(e[k]) if e[k] is not None else 0.0 for k in EDGE_PROPS}
    G.add_edge(src, dst, **props)

driver.close()
print(f"   {G.number_of_edges():,} arcs | {len(EDGE_PROPS)} propriétés/arc")

# Normalisation cost_time_trafic pour le routage adaptatif
ctt_vals  = [d["cost_time_trafic"] for _, _, d in G.edges(data=True)]
CTT_MIN   = 0.0
CTT_RANGE = max(ctt_vals) - CTT_MIN + 1e-9
print(f"   cost_time_trafic : max={max(ctt_vals):.1f} min → normalisé pour adaptatif")
print("\n✅ Graphe en mémoire — plus de requêtes Neo4j\n")

# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 2 — 300 PAIRES
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 62)
print("  ÉTAPE 2 — Génération des 300 paires (seed=42)")
print("=" * 62 + "\n")

random.seed(SEED); np.random.seed(SEED)
nodes_list = list(G.nodes())
nlat = {n: G.nodes[n]["lat"] for n in nodes_list}
nlng = {n: G.nodes[n]["lng"] for n in nodes_list}

def gen_pairs(cat, dmin, dmax, n=100, max_try=100000):
    pairs, tries = [], 0
    while len(pairs) < n and tries < max_try:
        s = random.choice(nodes_list); d = random.choice(nodes_list)
        if s == d: tries += 1; continue
        dist = haversine_km(nlat[s], nlng[s], nlat[d], nlng[d])
        if dmin <= dist < dmax:
            pairs.append({"source_id": s, "dest_id": d, "categorie": cat, "distance_km": round(dist, 3)})
        tries += 1
    print(f"   {cat:8s}: {len(pairs)} paires ({tries} tentatives)")
    return pairs

all_pairs = (
    gen_pairs("court", 0,    10,          N_PAIRS_EACH) +
    gen_pairs("moyen", 10,   50,          N_PAIRS_EACH) +
    gen_pairs("long",  50,   float("inf"),N_PAIRS_EACH)
)
pd.DataFrame(all_pairs).to_csv(PAIRS_CSV, index=False)
print(f"\n   {len(all_pairs)} paires → {PAIRS_CSV}\n")

# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 3 — ALGORITHMES
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 62)
print("  ÉTAPE 3 — 7 Algorithmes de routage")
print("=" * 62 + "\n")

raw = []

def add(src, dst, cat, algo, profil, t_ms, cout, hops, note=""):
    raw.append(dict(source_id=src, dest_id=dst, categorie=cat,
                    algo=algo, profil=profil,
                    temps_ms=round(t_ms, 3) if not math.isnan(t_ms) else NAN,
                    cout_total=round(cout, 6) if not math.isnan(cout) else NAN,
                    nb_hops=hops, succes=not math.isnan(cout), notes=note))

# ── 1. DIJKSTRA ───────────────────────────────────────────────────────────────
print("── ALGO 1 : Dijkstra (4 profils)")
for profil in PROFILES:
    for p in tqdm(all_pairs, desc=f"Dijk/{profil[:8]}", leave=False):
        s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
        t0 = time.perf_counter()
        try:
            path = nx.dijkstra_path(G, s, d, weight=profil)
            t_ms = (time.perf_counter() - t0)*1000
            add(s, d, cat, "1_dijkstra", profil, t_ms, path_cost(G, path, profil), path_hops(path))
        except Exception:
            add(s, d, cat, "1_dijkstra", profil, NAN, NAN, NAN, "no_path")
print("   ✅ Dijkstra terminé")

# ── 2. A* ─────────────────────────────────────────────────────────────────────
print("── ALGO 2 : A* (4 profils)")
for profil in PROFILES:
    for p in tqdm(all_pairs, desc=f"Astar/{profil[:8]}", leave=False):
        s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
        h = astar_heuristic(G, d, profil)
        t0 = time.perf_counter()
        try:
            path = nx.astar_path(G, s, d, heuristic=h, weight=profil)
            t_ms = (time.perf_counter() - t0)*1000
            add(s, d, cat, "2_astar", profil, t_ms, path_cost(G, path, profil), path_hops(path))
        except Exception:
            t_ms = (time.perf_counter() - t0)*1000   # ← temps enregistré même si pas de chemin
            add(s, d, cat, "2_astar", profil, t_ms, NAN, NAN, "no_path")
print("   ✅ A* terminé")

# ── 3. BELLMAN-FORD ───────────────────────────────────────────────────────────
print(f"── ALGO 3 : Bellman-Ford (4 profils, timeout={BF_TIMEOUT}s)")
for profil in PROFILES:
    for p in tqdm(all_pairs, desc=f"BF/{profil[:8]}", leave=False):
        s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
        fn   = lambda: nx.bellman_ford_path(G, s, d, weight=profil)
        t0   = time.perf_counter()
        path, err = run_timeout(fn, BF_TIMEOUT)
        t_ms = (time.perf_counter() - t0)*1000
        if path: add(s, d, cat, "3_bellman_ford", profil, t_ms, path_cost(G, path, profil), path_hops(path))
        else:    add(s, d, cat, "3_bellman_ford", profil, NAN, NAN, NAN, err or "no_path")
print("   ✅ Bellman-Ford terminé")

# ── 4. FLOYD-WARSHALL ─────────────────────────────────────────────────────────
print(f"── ALGO 4 : Floyd-Warshall (cost_dist, BFS {FW_BFS_SIZE} nœuds)")
for p in tqdm(all_pairs, desc="FW", leave=False):
    s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
    # BFS autour de s
    seen, bfs = {s}, [s]
    queue = list(G.successors(s))
    while queue and len(bfs) < FW_BFS_SIZE:
        n = queue.pop(0)
        if n not in seen:
            seen.add(n); bfs.append(n)
            queue.extend(G.successors(n))
    if d not in seen: bfs.append(d)
    subG = G.subgraph(bfs).copy()
    t0 = time.perf_counter()
    try:
        pred, dist_fw = nx.floyd_warshall_predecessor_and_distance(subG, weight="cost_dist")
        t_ms = (time.perf_counter() - t0)*1000
        if d in dist_fw.get(s, {}):
            path = nx.reconstruct_path(s, d, pred)
            add(s, d, cat, "4_floyd_warshall", "cost_dist", t_ms, dist_fw[s][d], path_hops(path), f"sub={len(bfs)}")
        else:
            add(s, d, cat, "4_floyd_warshall", "cost_dist", t_ms, NAN, NAN, "dest_hors_bfs")
    except Exception as e:
        add(s, d, cat, "4_floyd_warshall", "cost_dist", NAN, NAN, NAN, str(e)[:20])
print("   ✅ Floyd-Warshall terminé")

# ── 5. YEN K PLUS COURTS CHEMINS ─────────────────────────────────────────────
print(f"── ALGO 5 : Yen K={YEN_K} (cost_equilibre)")
for p in tqdm(all_pairs, desc="Yen", leave=False):
    s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
    t0 = time.perf_counter()
    try:
        paths = list(itertools.islice(nx.shortest_simple_paths(G, s, d, weight="cost_equilibre"), YEN_K))
        t_ms  = (time.perf_counter() - t0)*1000
        if paths:
            add(s, d, cat, "5_yen", "cost_equilibre", t_ms,
                path_cost(G, paths[0], "cost_equilibre"), path_hops(paths[0]), f"k={len(paths)}")
        else:
            add(s, d, cat, "5_yen", "cost_equilibre", t_ms, NAN, NAN, "no_path")
    except Exception:
        add(s, d, cat, "5_yen", "cost_equilibre", NAN, NAN, NAN, "no_path")
print("   ✅ Yen terminé")

# ── 6. PARETO MULTI-CRITÈRES ──────────────────────────────────────────────────
print(f"── ALGO 6 : Pareto (3 objectifs, {PARETO_N} chemins)")
OBJ = ["cost_dist", "cost_time_trafic", "cost_congestion_avg"]
for p in tqdm(all_pairs, desc="Pareto", leave=False):
    s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
    t0 = time.perf_counter()
    try:
        paths = list(itertools.islice(nx.shortest_simple_paths(G, s, d, weight="cost_dist"), PARETO_N))
        t_ms  = (time.perf_counter() - t0)*1000
        if not paths: raise nx.NetworkXNoPath
        sols = [tuple(path_cost(G, path, obj) for obj in OBJ) for path in paths]
        pf   = pareto_front(sols)
        best = paths[pf[0]]
        add(s, d, cat, "6_pareto", "multi_obj", t_ms,
            path_cost(G, best, "cost_dist"), path_hops(best), f"pareto={len(pf)}/{len(paths)}")
    except Exception:
        add(s, d, cat, "6_pareto", "multi_obj", NAN, NAN, NAN, "no_path")
print("   ✅ Pareto terminé")

# ── 7. ADAPTATIF TEMPS-RÉEL ───────────────────────────────────────────────────
jour     = DAY_NAMES_FR[datetime.now().weekday()]
cong_key = f"cost_congestion_{jour}"
print(f"── ALGO 7 : Adaptatif (jour={jour.upper()}, {cong_key})")

G_adapt = nx.DiGraph()
G_adapt.add_nodes_from(G.nodes(data=True))
for u, v, dd in G.edges(data=True):
    c1 = dd.get("cost_dist", 0)
    c3 = (dd.get("cost_time_trafic", 0) - CTT_MIN) / CTT_RANGE   # normalisé [0,1]
    c4 = dd.get(cong_key, dd.get("cost_congestion_avg", 0))
    G_adapt.add_edge(u, v, cost_adaptatif=round(0.10*c1 + 0.70*c3 + 0.20*c4, 6))

for p in tqdm(all_pairs, desc="Adaptatif", leave=False):
    s, d, cat = p["source_id"], p["dest_id"], p["categorie"]
    t0 = time.perf_counter()
    try:
        path = nx.dijkstra_path(G_adapt, s, d, weight="cost_adaptatif")
        t_ms = (time.perf_counter() - t0)*1000
        add(s, d, cat, "7_adaptatif", f"adaptatif_{jour}", t_ms,
            path_cost(G_adapt, path, "cost_adaptatif"), path_hops(path), f"jour={jour}")
    except Exception:
        add(s, d, cat, "7_adaptatif", f"adaptatif_{jour}", NAN, NAN, NAN, "no_path")
print("   ✅ Adaptatif terminé")

# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 4 — AGRÉGATION + EXPORT
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
print("  ÉTAPE 4 — Agrégation + Export CSV")
print("=" * 62 + "\n")

df_raw = pd.DataFrame(raw)
df_raw.to_csv(RAW_CSV, index=False)
print(f"   📄 Raw  : {len(df_raw):,} lignes → {RAW_CSV}")

df_agg = (df_raw.groupby(["algo", "profil", "categorie"])
          .agg(temps_ms_mean   =("temps_ms",   "mean"),
               temps_ms_std    =("temps_ms",   "std"),
               cout_total_mean =("cout_total", "mean"),
               cout_total_std  =("cout_total", "std"),
               nb_hops_mean    =("nb_hops",    "mean"),
               nb_hops_std     =("nb_hops",    "std"),
               taux_succes     =("succes",     "mean"),
               n_paires        =("succes",     "count"))
          .round(4).reset_index())
df_agg.to_csv(AGG_CSV, index=False)
print(f"   📊 Agg  : {len(df_agg):,} lignes → {AGG_CSV}\n")

# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 5 — ANALYSE CONSOLE
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 62)
print("  ÉTAPE 5 — Analyse console")
print("=" * 62)

CATS = ["court", "moyen", "long"]

def get_val(df, algo, profil, cat, col):
    sub = df[(df.algo == algo) & (df.profil == profil) & (df.categorie == cat)]
    if sub.empty: return "—"
    v = sub.iloc[0][col]
    return "—" if (isinstance(v, float) and math.isnan(v)) else v

# Tableau 1 : Temps moyen par algo (profil de référence par algo)
PROF_REF = {
    "1_dijkstra"     : "cost_rapide",
    "2_astar"        : "cost_rapide",
    "3_bellman_ford" : "cost_rapide",
    "4_floyd_warshall": "cost_dist",
    "5_yen"          : "cost_equilibre",
    "6_pareto"       : "multi_obj",
    "7_adaptatif"    : f"adaptatif_{jour}",
}
print("\n📊 Tableau 1 — Temps moyen (ms) par algorithme\n")
t1 = []
for algo, profil in PROF_REF.items():
    row = {"Algorithme": algo}
    for cat in CATS:
        v = get_val(df_agg, algo, profil, cat, "temps_ms_mean")
        row[cat] = f"{v:.1f}" if isinstance(v, float) else v
    t1.append(row)
print(tabulate(t1, headers="keys", tablefmt="rounded_outline", numalign="right"))

# Tableau 2 : Coût moyen par profil (Dijkstra)
print("\n📊 Tableau 2 — Coût moyen par profil Dijkstra (taux succès)\n")
t2 = []
for profil in PROFILES:
    row = {"Profil": profil}
    for cat in CATS:
        c = get_val(df_agg, "1_dijkstra", profil, cat, "cout_total_mean")
        s = get_val(df_agg, "1_dijkstra", profil, cat, "taux_succes")
        row[cat] = f"{c:.4f} ({float(s)*100:.0f}%)" if isinstance(c, float) and isinstance(s, float) else "—"
    t2.append(row)
print(tabulate(t2, headers="keys", tablefmt="rounded_outline", numalign="right"))

# Tableau 3 : Adaptatif vs cost_rapide Dijkstra
print(f"\n📊 Tableau 3 — Adaptatif ({jour.upper()}) vs Dijkstra/cost_rapide\n")
t3 = []
for cat in CATS:
    ca = get_val(df_agg, "7_adaptatif", f"adaptatif_{jour}", cat, "cout_total_mean")
    cr = get_val(df_agg, "1_dijkstra",  "cost_rapide",       cat, "cout_total_mean")
    ha = get_val(df_agg, "7_adaptatif", f"adaptatif_{jour}", cat, "nb_hops_mean")
    hr = get_val(df_agg, "1_dijkstra",  "cost_rapide",       cat, "nb_hops_mean")
    su = get_val(df_agg, "7_adaptatif", f"adaptatif_{jour}", cat, "taux_succes")
    if isinstance(ca, float) and isinstance(cr, float):
        dc = f"{ca - cr:+.4f}"; dh = f"{(ha or 0) - (hr or 0):+.1f}"
    else:
        dc = dh = "—"
    t3.append({"Catégorie": cat, "Coût adapt.": f"{ca:.4f}" if isinstance(ca, float) else "—",
                "Coût rapide": f"{cr:.4f}" if isinstance(cr, float) else "—",
                "Δ coût": dc, "Δ hops": dh,
                "Succès adapt.": f"{float(su)*100:.0f}%" if isinstance(su, float) else "—"})
print(tabulate(t3, headers="keys", tablefmt="rounded_outline", numalign="right"))

print(f"\n{'=' * 62}")
print("  ✅  TASK A — TERMINÉ")
print(f"{'=' * 62}")
print(f"""
  300 paires | 7 algorithmes | {len(df_raw):,} mesures brutes
  📄 pairs.csv           → {PAIRS_CSV}
  📄 raw results         → {RAW_CSV}
  📊 aggregated results  → {AGG_CSV}
""")
