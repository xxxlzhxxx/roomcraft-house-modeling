"""Reproducible local entry point. Blender is invoked only by Codex CLI + Kimi.

Run each review stage explicitly; --stage all reproduces the reviewed synthetic case.
Dependencies: Python 3.12+, requirements-demo.txt, npm ci, Codex CLI, Blender.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
COMMIT='0c833f84507904cb0ab7587980a7a71a902a26d0'
SOURCE='https://github.com/armedad/floorplan-editor.git'


def command(args, **kwargs):
    subprocess.run([str(a) for a in args],cwd=ROOT,check=True,**kwargs)


def prepare(run,samples,vendor):
    run.mkdir(parents=True,exist_ok=True);samples.mkdir(parents=True,exist_ok=True)
    for dest,src in [('floorplan-c.png','sheet.png'),('floorplan-c.json','plan.json')]:
        p=samples/dest
        if not p.exists():
            url=f'https://raw.githubusercontent.com/armedad/floorplan-editor/{COMMIT}/examples/sample/{src}'
            p.write_bytes(urllib.request.urlopen(url,timeout=30).read())
    if not vendor.exists():
        vendor.mkdir(parents=True);command(['git','init','-q',vendor]);command(['git','-C',vendor,'fetch','--depth','1',SOURCE,COMMIT]);command(['git','-C',vendor,'checkout','--detach','FETCH_HEAD'])
    actual=subprocess.check_output(['git','-C',str(vendor),'rev-parse','HEAD'],text=True).strip()
    if actual!=COMMIT:raise RuntimeError('Unexpected external editor revision; use the pinned commit')
    calibration={'origin_px':[30,30],'meters_per_pixel':.0127,'scale_source':'Explicit companion sample calibration: 2 px/inch','wall_height_m':2.75,'height_source':'Assumed demo height','interior_bounds_m':[.1,.1,10.06,6.5],'zones':[{'id':'living','label':'客餐厅/开放厨房','bounds':[0,0,7.5,6.6]},{'id':'bedroom','label':'卧室','bounds':[6.1,2.8,10.16,6.6]},{'id':'bath','label':'卫生间','bounds':[7.62,0,10.16,2.79]}],'zone_source':'Manual approximate material/camera zones, not extracted room polygons'}
    reviews={'openings':{f'opening-{i}':{'kind':'window' if i in (1,5) else 'door','sill':.9 if i in (1,5) else 0,'height':1.5 if i in (1,5) else 2.15} for i in range(1,6)}}
    for name,data in [('calibration.json',calibration),('opening-review.json',reviews)]:
        if not (run/name).exists():(run/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    if not (run/'request.txt').exists():(run/'request.txt').write_text('保持墙体门窗和柱子不变。客厅采用轻奢简约风：2.2米奶油色软包沙发朝向电视，岩板茶几、带少量图案的地毯，圆餐桌和四把椅子。卧室用1.8米软包床和衣柜。厨房、卫生间按原图配置橱柜、水槽、灶台、冰箱、台盆、马桶、浴缸。客餐厅有纹理大理石，卧室木地板，影木柜子，无装饰画。门洞与行走通道保持畅通。',encoding='utf8')


def kimi(run,name,prompt,reasoning):
    task=run/(name+'-task.txt');task.write_text(prompt,encoding='utf8')
    command([sys.executable,ROOT/'scripts/spatial/run_codex_kimi.py',task,'--output',run/(name+'-execution.jsonl'),'--sandbox','danger-full-access','--reasoning',reasoning])


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['prepare','recognize','edit','build','package','all'],default='prepare');p.add_argument('--run-dir',type=Path,default=ROOT/'runs/open-source-demo');p.add_argument('--request',type=Path);p.add_argument('--reuse-operations',action='store_true',help='Replay existing edit.json instead of making a new language-model call');p.add_argument('--blender',default='/Applications/Blender.app/Contents/MacOS/Blender');a=p.parse_args();run=a.run_dir.resolve();samples=ROOT/'inputs/research-samples';vendor=run/'vendor/floorplan-editor';stages=['prepare','recognize','edit','build','package'] if a.stage=='all' else [a.stage]
    for stage in stages:
        if stage=='prepare':prepare(run,samples,vendor)
        elif stage=='recognize':
            env=os.environ.copy();env['PYTHONPATH']=str(vendor)
            command([sys.executable,'-m','floorplan_editor','trace',samples/'floorplan-c.png','--px-per-inch','2','--min-thickness','8','--max-thickness','36','--min-length','22','--out',run/'opensource-trace.json'],env=env)
            pipeline=ROOT/'scripts/spatial/floorplan_pipeline.py'
            command([sys.executable,pipeline,'detect','--input',samples/'floorplan-c.png','--trace',run/'opensource-trace.json','--config',run/'calibration.json','--output',run/'detected.json','--image',samples/'floorplan-c.png'])
            command([sys.executable,pipeline,'review','--input',run/'detected.json','--config',run/'opening-review.json','--output',run/'reviewed.json','--image',samples/'floorplan-c.png'])
            command([sys.executable,pipeline,'export-editor','--input',run/'reviewed.json','--output',run/'editor/plan.json'])
            shutil.copy2(samples/'floorplan-c.png',run/'editor/sheet.png')
            command([sys.executable,'-m','floorplan_editor','validate',run/'editor/plan.json'],env=env)
        elif stage=='edit':
            if a.reuse_operations:
                pipeline=ROOT/'scripts/spatial/floorplan_pipeline.py'
                command([sys.executable,pipeline,'edit','--input',run/'reviewed.json','--config',run/'edit.json','--output',run/'edited.json'])
                command([sys.executable,pipeline,'validate','--input',run/'edited.json','--output',run/'validation.json'])
                command([sys.executable,ROOT/'scripts/spatial/check_layout.py','--scene',run/'edited.json','--output',run/'layout-validation.json'])
                continue
            if a.request:shutil.copy2(a.request,run/'request.txt')
            prompt=f'''Follow AGENTS.md. You are the required Codex CLI configured for Kimi K3.1. Do not operate Blender in this task. Read {run}/reviewed.json, {run}/request.txt and scripts/spatial/floorplan_pipeline.py. Translate the request to {run}/edit.json with operations add_furniture/update_furniture only. New furniture uses id,type,center[x,y] meters,size[width,depth,height] meters,rotation_deg,color,material. Walls and openings immutable. Write complete explicit operations, apply with floorplan_pipeline.py edit to {run}/edited.json and validate to {run}/validation.json. Check furniture collisions, column and door clearances; rugs intentionally overlap allowed. Record actual checks and limitations. Do not read/print private inventory, env secrets, endpoints. Do not commit or push. Use {sys.executable} for Python.'''
            kimi(run,'edit',prompt,'medium')
            command([sys.executable,ROOT/'scripts/spatial/check_layout.py','--scene',run/'edited.json','--output',run/'layout-validation.json'])
        elif stage=='build':
            builder=ROOT/'scripts/spatial/build_raster_demo.py'
            if not builder.exists():raise RuntimeError('The Kimi-authored reusable Blender builder is missing')
            prompt=f'''Follow AGENTS.md. Every Blender operation in this task must be executed by you, Codex CLI configured for Kimi K3.1. Run existing {builder} using Blender binary {a.blender!r}. Read script --arguments in source, do not invoke Blender outside this CLI. Build {run}/edited.json into {run}/edited with --render (scene.blend/model.glb/overview.png/living.png) and {run}/reviewed.json into {run}/baseline without render. Retain run logs and reports, verify actual files and metric layout. Debug failures in your authored builder and rerun as needed. Do not print secrets, inspect private inventory, commit, push or change global settings.'''
            kimi(run,'build',prompt,'medium')
        elif stage=='package':command([sys.executable,ROOT/'scripts/spatial/package_floorplan_demo.py','--run-dir',run,'--sample-dir',samples])


if __name__=='__main__':main()
