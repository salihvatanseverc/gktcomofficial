import requests
import numpy as np
from PIL import Image

print("=== GÖKTÜRK PYTHON PAKET TESTİ ===")

print("Requests:", requests.__version__)

print("NumPy:", np.__version__)
print("Array:", np.arange(10))

img = Image.new("RGB", (64, 64), "red")
print("Pillow:", img.size)

r = requests.models.Response()
r.status_code = 200
print("Requests Response:", r.status_code)

print("\nSONUÇ: TÜM PAKETLER BAŞARIYLA ÇALIŞTI")