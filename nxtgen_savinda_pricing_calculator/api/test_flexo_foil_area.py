import unittest
from unittest.mock import Mock, patch
import frappe
from nxtgen_savinda_pricing_calculator.api.offset_calculator import _flexo_foil_area, _calc_spec_foil_cost


class TestFlexoFoilArea(unittest.TestCase):
    def test_area_choice_and_old_snapshot_default(self):
        sheet = {'reel_area_net': 100, 'reel_area': 150}
        self.assertEqual(_flexo_foil_area({}, sheet), 100)
        self.assertEqual(_flexo_foil_area({'flexo_foil_area_basis':'Total Reel Area'}, sheet), 150)
        self.assertEqual(_flexo_foil_area({}, {'reel_area_net':0}, 150), 0)
        self.assertEqual(_flexo_foil_area({}, {}, 150), 150)

    def test_foil_and_varnish_use_selected_area_with_existing_minimums(self):
        if not hasattr(frappe.local, 'db'):
            frappe.local.db=Mock()
            self.addCleanup(lambda: delattr(frappe.local, 'db'))
        foil=frappe._dict(cost_per_sqm=10,min_qty=0,min_value=0)
        varnish=frappe._dict(consumption_per_sqm=.01,price_per_kg=100,min_qty=.2)
        assignment={'foils':[{'foil_key':'COLD-Silver','foil_group':'COLD','percentage':50}]}
        with patch.object(frappe.db,'exists',return_value=True), patch.object(frappe,'get_cached_doc',side_effect=lambda dt,name:foil if dt=='Flexo Foil' else varnish):
            for basis,area in [('Net Reel Area',100),('Total Reel Area',150)]:
                selected=_flexo_foil_area({'flexo_foil_area_basis':basis},{'reel_area_net':100,'reel_area':150})
                rows,total=_calc_spec_foil_cost('Flexo Foils',assignment,selected)
                self.assertEqual(rows[0]['req_qty'],area*.5*1.25)
                self.assertEqual(rows[1]['req_qty'],area*.01)
                self.assertEqual(total,area*.5*1.25*10+area*.01*100)
            rows,_=_calc_spec_foil_cost('Flexo Foils',assignment,1)
            self.assertEqual(rows[1]['req_qty'],.2)
