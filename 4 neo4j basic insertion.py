"""
4 neo4j basic insertion.py
--------------------------
But : Insérer les capteurs (nœuds) et les routes (arcs)
      dans Neo4j depuis ca_meta.csv et edges.csv
"""

import pandas as pd
from neo4j import GraphDatabase
from tqdm import tqdm

# ─── CONFIG ───────────────────────────────────────────────────────────────────
NEO4J_URI      = "bolt://localhost:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = "password123"

META_PATH  = "/root/Desktop/TG/data/ca_meta.csv"
EDGES_PATH = "/root/Desktop/TG/data/edges.csv"
BATCH_SIZE = 500   # nb de lignes insérées par transaction

# ─── CONNEXION ────────────────────────────────────────────────────────────────
print("🔌 Connexion à Neo4j...")
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
driver.verify_connectivity()
print("   ✅ Connecté !\n")

# ─── CHARGEMENT CSV ───────────────────────────────────────────────────────────
print("📂 Chargement des fichiers...")
meta  = pd.read_csv(META_PATH)
edges = pd.read_csv(EDGES_PATH)
print(f"   Capteurs : {len(meta)}")
print(f"   Arcs     : {len(edges)}\n")

# ─── NETTOYAGE ────────────────────────────────────────────────────────────────
with driver.session() as session:
    session.run("MATCH (n) DETACH DELETE n")
    print("🧹 Base vidée\n")

# ─── INDEX ────────────────────────────────────────────────────────────────────
with driver.session() as session:
    session.run("CREATE INDEX sensor_index IF NOT EXISTS FOR (s:Sensor) ON (s.index)")
    print("📌 Index créé sur Sensor.index\n")

# ─── INSERTION NŒUDS ──────────────────────────────────────────────────────────
print(f"🔵 Insertion des nœuds Sensor (batch={BATCH_SIZE})...")

nodes = meta.rename(columns={
    "ID": "id", "Lat": "lat", "Lng": "lng",
    "District": "district", "County": "county",
    "Fwy": "fwy", "Lanes": "lanes",
    "Type": "type", "Direction": "direction", "ID2": "id2"
}).copy()
nodes.insert(0, "index", range(len(nodes)))

node_records = nodes.to_dict("records")

def insert_nodes(tx, batch):
    tx.run("""
        UNWIND $batch AS row
        CREATE (:Sensor {
            index    : row.index,
            id       : row.id,
            lat      : row.lat,
            lng      : row.lng,
            district : row.district,
            county   : row.county,
            fwy      : row.fwy,
            lanes    : row.lanes,
            type     : row.type,
            direction: row.direction
        })
    """, batch=batch)

with driver.session() as session:
    for i in tqdm(range(0, len(node_records), BATCH_SIZE), desc="Nœuds", unit="batch"):
        batch = node_records[i : i + BATCH_SIZE]
        session.execute_write(insert_nodes, batch)

print(f"   ✅ {len(node_records)} nœuds insérés !\n")

# ─── INSERTION ARCS ───────────────────────────────────────────────────────────
print(f"🔴 Insertion des arcs ROUTE (batch={BATCH_SIZE})...")

edge_records = edges.to_dict("records")

def insert_edges(tx, batch):
    tx.run("""
        UNWIND $batch AS row
        MATCH (a:Sensor {index: row.from_id})
        MATCH (b:Sensor {index: row.to_id})
        CREATE (a)-[:ROUTE {
            distance_km : row.distance_km,
            duration_min: row.duration_min,
            weight_adj  : row.weight_adj
        }]->(b)
    """, batch=batch)

with driver.session() as session:
    for i in tqdm(range(0, len(edge_records), BATCH_SIZE), desc="Arcs  ", unit="batch"):
        batch = edge_records[i : i + BATCH_SIZE]
        session.execute_write(insert_edges, batch)

print(f"   ✅ {len(edge_records)} arcs insérés !\n")

# ─── VÉRIFICATION ─────────────────────────────────────────────────────────────
with driver.session() as session:
    n_nodes = session.run("MATCH (s:Sensor) RETURN count(s) AS n").single()["n"]
    n_edges = session.run("MATCH ()-[r:ROUTE]->() RETURN count(r) AS n").single()["n"]

print("=" * 45)
print("  ✅ INSERTION TERMINÉE")
print("=" * 45)
print(f"  🔵 Nœuds Sensor : {n_nodes}")
print(f"  🔴 Arcs ROUTE   : {n_edges}")
print(f"  🌐 Neo4j Browser : http://localhost:7474")
print("=" * 45)

driver.close()
