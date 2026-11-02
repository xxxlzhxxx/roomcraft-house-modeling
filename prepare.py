"""Prepare an isolated run from a locally supplied, private RoomCraft bundle."""
import argparse
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--bundle', type=Path, required=True, help='Extracted private case bundle directory')
p.add_argument('--name', required=True, help='New run name: letters, numbers, hyphen, underscore')
a = p.parse_args()
if not a.name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in a.name):
    p.error('Use letters, digits, hyphen and underscore for --name')
bundle = a.bundle.resolve()
target = ROOT / 'runs' / a.name
source_scene = bundle / 'inputs/base-scene.blend'
config_path = bundle / 'templates/standard/config.json'
material_dir = bundle / 'templates/standard/material-sources'
for path in (source_scene, config_path, material_dir):
    if not path.exists():
        p.error(f'Private bundle is missing {path.relative_to(bundle)}')
if target.exists():
    p.error('Run already exists; choose a different --name')
config = json.loads(config_path.read_text())
for rel in config['materialSources'].values():
    path = (bundle / 'templates/standard' / rel).resolve()
    if not path.is_relative_to(material_dir.resolve()) or not path.is_file():
        p.error('A material source is missing or outside material-sources/')
scene_target = ROOT / 'inputs/base-scene.blend'
if scene_target.exists() and scene_target.read_bytes() != source_scene.read_bytes():
    p.error('A different private source scene already exists; use a separate checkout')
scene_target.parent.mkdir(parents=True, exist_ok=True)
if not scene_target.exists():
    shutil.copy2(source_scene, scene_target)
shutil.copytree(ROOT / 'templates/standard', target)
shutil.copytree(material_dir, target / 'material-sources')
# Make the source path predictable for this standalone checkout.
config['source'] = '../../inputs/base-scene.blend'
(target / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
print(f'Prepared runs/{a.name}. This did not launch Blender or call a model.')
