import numpy as np

PATH = "/root/Desktop/TG/data/ca_rn_adj.npy"

mat = np.load(PATH)

print("=" * 50)
print("        RÉSUMÉ : ca_rn_adj.npy")
print("=" * 50)
print(f"  Shape              : {mat.shape}")
print(f"  Lignes             : {mat.shape[0]}")
print(f"  Colonnes           : {mat.shape[1]}")
print(f"  dtype              : {mat.dtype}")
print(f"  Taille mémoire     : {mat.nbytes / 1e6:.2f} MB")
print("-" * 50)
print(f"  Valeur min         : {mat.min():.6f}")
print(f"  Valeur max         : {mat.max():.6f}")
print(f"  Valeur moyenne     : {mat.mean():.6f}")
print(f"  Écart-type         : {mat.std():.6f}")
print("-" * 50)
total = mat.size
zeros = (mat == 0).sum()
nonzeros = (mat != 0).sum()
print(f"  Zéros              : {zeros} ({zeros/total*100:.1f}%)")
print(f"  Non-zéros          : {nonzeros} ({nonzeros/total*100:.1f}%)")
print("-" * 50)
print(f"  Matrice carrée     : {mat.shape[0] == mat.shape[1]}")
print(f"  Matrice symétrique : {np.allclose(mat, mat.T)}")
print(f"  Diagonale (5 val)  : {mat.diagonal()[:5]}")
print("-" * 50)
print("  Aperçu coin [0:4, 0:4] :")
print(mat[:4, :4])
print("=" * 50)
