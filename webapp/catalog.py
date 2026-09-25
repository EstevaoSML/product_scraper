"""Fonte substituível: preços em centavos, mesma variante durante a série."""
from copy import deepcopy

MONTHS = ['2025-09-22', '2025-10-22', '2025-11-28', '2025-12-25', '2026-01-22', '2026-02-22', '2026-03-22', '2026-04-22', '2026-05-22', '2026-06-22', '2026-07-22', '2026-08-22', '2026-09-22']
ROWS = [
    ('tv', 'Smart TV 55” Crystal 4K', 'Televisões', '55 polegadas · LED · Wi-Fi', '1593784991095-a205069470b6', [3299,3499,2899,3199,3099,2999,3099,2899,2799,2899,2699,2799,2499]),
    ('geladeira', 'Geladeira Frost Free Duplex', 'Eletrodomésticos', '375 litros · Inox · 127 V', '1571175443880-49e1d25b2bc5', [3199,3299,2999,3499,3399,3299,3199,3399,3299,3199,3099,2999,2899]),
    ('notebook', 'Notebook ultrafino 15,6”', 'Informática', '16 GB RAM · SSD 512 GB · Prata', '1496181133206-80ce9b88a853', [4599,4899,4299,4699,4499,4299,4199,4299,4099,3999,4199,3999,3799]),
    ('fone', 'Fone sem fio com cancelamento', 'Áudio', 'Bluetooth · Over-ear · Preto', '1546435770-a3e426bf472b', [599,649,499,579,549,529,499,479,499,459,429,449,399]),
    ('lavadora', 'Máquina de lavar 12 kg', 'Eletrodomésticos', 'Abertura frontal · Branca · 127 V', '1626806787461-102c1bfaaea1', [2199,2299,2099,2499,2299,2199,2299,2399,2299,2399,2499,2399,2299]),
    ('cafeteira', 'Cafeteira espresso compacta', 'Cozinha', '15 bar · Reservatório 1,2 L · 127 V', '1517668808822-9ebb02f2a0e6', [799,899,699,849,799,749,779,749,699,729,699,679,649]),
]


class DemoCatalog:
    def list_products(self):
        result = []
        for pid, name, category, variant, photo, values in ROWS:
            history = [dict(date=date, price_cents=price*100, event='Black Friday' if i == 2 else 'Natal' if i == 3 else None) for i, (date, price) in enumerate(zip(MONTHS, values))]
            result.append(dict(id=pid, name=name, category=category, variant=variant,
                image=f'https://images.unsplash.com/photo-{photo}?auto=format&fit=crop&w=720&q=80',
                current_cents=values[-1]*100, year_ago_cents=values[0]*100,
                change=round((values[-1]/values[0]-1)*100, 1), history=history,
                currency='BRL', source='Simulação', seller='Loja demonstrativa'))
        return deepcopy(result)

    def get_product(self, product_id):
        return next((p for p in self.list_products() if p['id'] == product_id), None)
