"""Kimi-authored 2D checks retained from the demo CLI; parent added wall probes.
Not a structural or accessibility certification.
"""
import json, math
from pathlib import Path
import argparse
parser=argparse.ArgumentParser()
parser.add_argument('--scene',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--routes',type=Path,help='Optional case-specific route polyline JSON')
args=parser.parse_args()
scene=json.loads(args.scene.read_text())
furn=scene['furniture']

def axes(f):
    a=math.radians(f.get('rotation_deg',0))
    return (math.cos(a),math.sin(a)),(-math.sin(a),math.cos(a))

def projection_radius(f, axis):
    u,v=axes(f); w,d,_=f['size']
    return abs(axis[0]*u[0]+axis[1]*u[1])*w/2+abs(axis[0]*v[0]+axis[1]*v[1])*d/2

def obb_overlap(a,b,margin=0):
    axes_a=axes(a); axes_b=axes(b)
    for axis in (*axes_a,*axes_b):
        distance=abs(axis[0]*(a['center'][0]-b['center'][0])+axis[1]*(a['center'][1]-b['center'][1]))
        if distance >= projection_radius(a,axis)+projection_radius(b,axis)+margin-1e-7:
            return False
    return True

def supported(a,b):
    if b['type']!='counter' or a['type'] not in {'sink','stove'}: return False
    u,v=axes(b); dx=a['center'][0]-b['center'][0]; dy=a['center'][1]-b['center'][1]
    return abs(dx*u[0]+dy*u[1]) <= b['size'][0]/2 and abs(dx*v[0]+dy*v[1]) <= b['size'][1]/2

pair_collisions=[]; intentional=[]
for i,a in enumerate(furn):
    for b in furn[i+1:]:
        if not obb_overlap(a,b): continue
        reason=None
        if 'rug' in {a['type'],b['type']}: reason='rug_under_furniture'
        elif supported(a,b) or supported(b,a): reason='fixture_on_counter'
        if reason: intentional.append({'a':a['id'],'b':b['id'],'reason':reason})
        else: pair_collisions.append({'a':a['id'],'b':b['id']})
column_collisions=[]
for f in furn:
    if f['type']=='rug': continue
    for c in scene['columns']:
        box={'center':c['center'],'size':[c['size'][0],c['size'][1],2.75],'rotation_deg':0}
        if obb_overlap(f,box): column_collisions.append({'furniture':f['id'],'column':c['id']})
walls={w['id']:w for w in scene['walls']}
door_zones=[]
for o in scene['openings']:
    if o['kind']!='door': continue
    w=walls[o['wall']]; length=math.dist(w['a'],w['b']); ux=(w['b'][0]-w['a'][0])/length; uy=(w['b'][1]-w['a'][1])/length; nx,ny=-uy,ux
    start=[w['a'][0]+ux*o['offset'],w['a'][1]+uy*o['offset']]
    center=[start[0]+ux*o['width']/2,start[1]+uy*o['width']/2]
    # Rectangle centered on the wall, extending 0.65 m into both adjoining spaces.
    zone={'center':center,'size':[o['width'],1.3,2.15],'rotation_deg':math.degrees(math.atan2(uy,ux))}
    blocked=[]
    for f in furn:
        if f['type']!='rug' and obb_overlap(f,zone): blocked.append(f['id'])
    door_zones.append({'opening':o['id'],'blocked_by':blocked,'clear':not blocked})
routes={
 'entrance_to_kitchen':[[2.6,6.3],[4.4,5.8],[4.8,4.3],[4.8,3.75],[2.7,3.75],[2.7,3.05]],
 'entrance_to_bedroom':[[2.6,6.3],[4.4,5.8],[4.8,4.3],[4.8,3.75],[6.75,3.7],[7.0,4.0]],
 'bedroom_to_bath':[[6.75,3.7],[8.5,3.5],[8.5,2.5],[8.5,1.4]]
}
if args.routes: routes=json.loads(args.routes.read_text())
obstacles=[f for f in furn if f['type']!='rug']+[{'id':c['id'],'center':c['center'],'size':[c['size'][0],c['size'][1],2.75],'rotation_deg':0} for c in scene['columns']]
for w in scene['walls']:
    length=math.dist(w['a'],w['b']);ux=(w['b'][0]-w['a'][0])/length;uy=(w['b'][1]-w['a'][1])/length;cursor=0
    gaps=sorted((o for o in scene['openings'] if o['wall']==w['id'] and o['kind']=='door'),key=lambda o:o['offset'])
    for stop,next_start in [(o['offset'],o['offset']+o['width']) for o in gaps]+[(length,length)]:
        if stop>cursor:
            center=(cursor+stop)/2
            obstacles.append({'id':w['id'],'center':[w['a'][0]+ux*center,w['a'][1]+uy*center],'size':[stop-cursor,w['thickness'],w['height']],'rotation_deg':math.degrees(math.atan2(uy,ux))})
        cursor=next_start
route_results=[]
for name,points in routes.items():
    blocked=set()
    for p,q in zip(points,points[1:]):
        steps=max(1,math.ceil(math.dist(p,q)/0.04))
        for i in range(steps+1):
            t=i/steps; probe={'center':[p[0]+(q[0]-p[0])*t,p[1]+(q[1]-p[1])*t],'size':[0.42,0.42,1.7],'rotation_deg':0}
            for obstacle in obstacles:
                if obb_overlap(probe,obstacle): blocked.add(obstacle['id'])
    route_results.append({'route':name,'clear':not blocked,'blocked_by':sorted(blocked),'probe_width_m':0.42})
passed=not pair_collisions and not column_collisions and all(x['clear'] for x in door_zones) and all(x['clear'] for x in route_results)
report={'status':'passed' if passed else 'failed','furniture_count':len(furn),'checks':['furniture_furniture_obb','furniture_column_obb','doorway_clearance_0.65m_each_side','sampled_routes_with_0.42m_probe_including_walls'],'unexpected_furniture_collisions':pair_collisions,'intentional_overlaps':intentional,'column_collisions':column_collisions,'doorways':door_zones,'routes':route_results,'limitations':['2D clearance checks use declared furniture envelopes, not detailed mesh contours','Routes demonstrate representative access paths, not exhaustive navigation mesh coverage','Default routes are specific to the synthetic sample; provide --routes for other layouts','Rug overlaps and counter-mounted sink/stove overlaps are explicitly allowed']}
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
if not passed: raise SystemExit(1)
