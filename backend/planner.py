"""Occupancy-grid planning shared by Virtual Lab and route validation."""
from __future__ import annotations

import heapq
import math
from .domain import map_version

def world_to_cell(grid,p):
    o=grid['origin'];a=o.get('yaw',0);dx=p['x']-o['x'];dy=p['y']-o['y'];r=grid['resolution']
    return int((math.cos(a)*dx+math.sin(a)*dy)/r),int((-math.sin(a)*dx+math.cos(a)*dy)/r)

def cell_to_world(grid,c):
    o=grid['origin'];a=o.get('yaw',0);r=grid['resolution'];x=(c[0]+.5)*r;y=(c[1]+.5)*r
    return {'x':o['x']+math.cos(a)*x-math.sin(a)*y,'y':o['y']+math.sin(a)*x+math.cos(a)*y,'yaw':0.}

def clearance_cells(grid,radius_m):
    w,h=grid['width'],grid['height'];data=grid['data'];radius=max(1,math.ceil(radius_m/grid['resolution']))
    blocked={(x,y) for y in range(h) for x in range(w) if data[y*w+x] != 0}
    inflated=set(blocked)
    for x,y in blocked:
        for dy in range(-radius,radius+1):
            for dx in range(-radius,radius+1):
                if dx*dx+dy*dy<=radius*radius:inflated.add((x+dx,y+dy))
    # The map edge is a wall for a robot with a footprint, not free infinity.
    inflated.update((x,y) for y in range(h) for x in range(w) if x<radius or y<radius or x>=w-radius or y>=h-radius)
    return inflated

def snap_free(grid,p,inflated,max_distance=.25):
    w,h=grid['width'],grid['height'];start=world_to_cell(grid,p);limit=math.ceil(max_distance/grid['resolution'])
    candidates=[]
    for dy in range(-limit,limit+1):
        for dx in range(-limit,limit+1):
            c=(start[0]+dx,start[1]+dy)
            if 0<=c[0]<w and 0<=c[1]<h and c not in inflated:
                d=math.hypot(dx,dy)
                if d<=limit:candidates.append((d,c))
    return min(candidates)[1] if candidates else None

def _line_clear(a,b,blocked):
    x0,y0=a;x1,y1=b;steps=max(abs(x1-x0),abs(y1-y0),1)
    return all((round(x0+(x1-x0)*i/steps),round(y0+(y1-y0)*i/steps)) not in blocked for i in range(steps+1))

def plan_segment(grid,start,goal,radius_m):
    blocked=clearance_cells(grid,radius_m)
    a=snap_free(grid,start,blocked);b=snap_free(grid,goal,blocked)
    if a is None or b is None:return None,'Point is occupied, unknown, or lacks footprint clearance'
    moves=((1,0,1.),(-1,0,1.),(0,1,1.),(0,-1,1.),(1,1,1.414),(-1,1,1.414),(1,-1,1.414),(-1,-1,1.414))
    q=[(0.,a)];cost={a:0.};parent={a:None};w,h=grid['width'],grid['height']
    while q:
        _,cur=heapq.heappop(q)
        if cur==b:break
        for dx,dy,step in moves:
            nxt=(cur[0]+dx,cur[1]+dy)
            if not (0<=nxt[0]<w and 0<=nxt[1]<h) or nxt in blocked:continue
            if dx and dy and ((cur[0]+dx,cur[1]) in blocked or (cur[0],cur[1]+dy) in blocked):continue
            near=sum((nxt[0]+ox,nxt[1]+oy) in blocked for ox,oy in ((1,0),(-1,0),(0,1),(0,-1)))
            score=cost[cur]+step+near*.35
            if score<cost.get(nxt,float('inf')):
                cost[nxt]=score;parent[nxt]=cur
                heapq.heappush(q,(score+math.hypot(nxt[0]-b[0],nxt[1]-b[1]),nxt))
    if b not in parent:return None,'No collision-free path exists'
    cells=[];cur=b
    while cur is not None:cells.append(cur);cur=parent[cur]
    cells.reverse();smooth=[cells[0]];i=0
    while i<len(cells)-1:
        j=len(cells)-1
        while j>i+1 and not _line_clear(cells[i],cells[j],blocked):j-=1
        smooth.append(cells[j]);i=j
    poses=[cell_to_world(grid,c) for c in smooth];poses[-1]['yaw']=goal.get('yaw',0.)
    distance=sum(math.hypot(v['x']-u['x'],v['y']-u['y']) for u,v in zip(poses,poses[1:]))
    return {'poses':poses,'distance_m':round(distance,3),'snapped_goal':cell_to_world(grid,b)},None

def plan_route(grid,start,points,radius_m,speed=.06):
    segments=[];errors=[];cursor=start;total=0.
    for i,p in enumerate(points):
        segment,error=plan_segment(grid,cursor,p,radius_m)
        if error:errors.append({'checkpoint':i+1,'message':error})
        else:
            segment['checkpoint']=i+1;segments.append(segment);total+=segment['distance_m'];cursor=p
    return {'valid':not errors,'map_version':map_version(grid),'segments':segments,'errors':errors,'distance_m':round(total,3),'eta_seconds':round(total/max(speed,.01),1)}
