import kaggle
import os

# Dossier de destination
DEST = "/root/Desktop/TG/data"
os.makedirs(DEST, exist_ok=True)

DATASET = "liuxu77/largest"

FILES = [
    "ca_his_raw_2021.h5",
    "ca_meta.csv",
    "ca_rn_adj.npy",
]

print("🚀 Début du téléchargement...\n")

for f in FILES:
    print(f"📥 Téléchargement de {f} ...")
    kaggle.api.dataset_download_file(
        dataset=DATASET,
        file_name=f,
        path=DEST,
        force=False,   # ne re-télécharge pas si déjà présent
    )
    # Deziper si nécessaire
    zip_path = os.path.join(DEST, f + ".zip")
    if os.path.exists(zip_path):
        import zipfile
        print(f"📦 Extraction de {f}.zip ...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(DEST)
        os.remove(zip_path)
        print(f"✅ {f} extrait et prêt !\n")
    else:
        print(f"✅ {f} téléchargé !\n")

print("🎉 Tous les fichiers sont dans :", DEST)
print(os.listdir(DEST))
