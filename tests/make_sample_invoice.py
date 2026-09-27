from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
p=Path(__file__).resolve().parent/'sample_invoice.png'
img=Image.new('RGB',(1800,1500),'white');d=ImageDraw.Draw(img)
font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',42)
text='''ABC TRADERS PVT LTD
TAX INVOICE
GSTIN: 27ABCDE1234F1Z5
Invoice No: INV-2048
Invoice Date: 08/09/2026

Taxable Value: 50000.00
CGST: 4500.00
SGST: 4500.00
IGST: 0.00
Grand Total: 59000.00
'''
d.multiline_text((100,100),text,font=font,fill='black',spacing=24)
img.save(p)
print(p)
