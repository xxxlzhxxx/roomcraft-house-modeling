import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/spatial'))
from floorplan_pipeline import apply_edit, validate, editor_plan, import_editor


def scene():
    return {'units':'m','calibration':{'interior_bounds_m':[0,0,6,6],'wall_height_m':2.75,'meters_per_pixel':.01,'origin_px':[0,0]},'image_size':[600,600],'walls':[{'id':'w','a':[3,0],'b':[3,6],'height':2.75,'thickness':.2}],'openings':[{'id':'door','wall':'w','offset':1,'width':.9,'kind':'door'}],'furniture':[],'columns':[]}


class PipelineChecks(unittest.TestCase):
    def test_rejects_object_intersecting_wall_and_preserves_original(self):
        s=scene();before=copy.deepcopy(s)
        with self.assertRaisesRegex(ValueError,'intersects wall'):
            apply_edit(s,{'operations':[{'op':'add_furniture','value':{'id':'sofa','center':[2.7,4],'size':[1,2,.8]}}]})
        self.assertEqual(s,before)

    def test_rotated_footprint_must_stay_inside(self):
        s=scene();s['furniture']=[{'id':'f','center':[.5,.5],'size':[1,1,.7],'rotation_deg':45}]
        with self.assertRaisesRegex(ValueError,'outside building'): validate(s)

    def test_opening_cannot_extend_beyond_wall(self):
        s=scene();s['openings'][0]['offset']=5.5
        with self.assertRaisesRegex(ValueError,'outside wall'): validate(s)

    def test_duplicate_objects_are_rejected(self):
        s=scene();s['furniture']=[{'id':'w','center':[1,1],'size':[1,1,1]}]
        with self.assertRaisesRegex(ValueError,'Duplicate'): validate(s)

    def test_editor_roundtrip_preserves_metric_geometry(self):
        s=scene();result=import_editor(editor_plan(s),s)
        for a,b in zip(result['walls'][0]['b'],s['walls'][0]['b']):self.assertAlmostEqual(a,b)
        self.assertAlmostEqual(result['openings'][0]['width'],.9)
        self.assertTrue(result['editor_import']['review_required'])


if __name__=='__main__':unittest.main()
