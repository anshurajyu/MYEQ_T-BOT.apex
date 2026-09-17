"""ROS 2 adapter, imported only when --ros is selected. Never invents sensor data."""
import math
import os
import threading
import time

def yaw(q):return math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))

class RosAdapter:
    def __init__(self):
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy, ReliabilityPolicy
        from rclpy.action import ActionClient
        from sensor_msgs.msg import LaserScan, BatteryState
        from nav_msgs.msg import OccupancyGrid, Odometry
        from geometry_msgs.msg import Twist, TwistStamped
        from nav2_msgs.action import NavigateToPose, ComputePathToPose
        from tf2_ros import Buffer, TransformListener
        self.rclpy=rclpy;self.Twist=Twist;self.TwistStamped=TwistStamped;self.NavigateToPose=NavigateToPose;self.ComputePathToPose=ComputePathToPose
        rclpy.init();self.node=Node('tbot_console_gateway');self.node.set_parameters([rclpy.parameter.Parameter('use_sim_time',value=os.environ.get('TBOT_USE_SIM_TIME','false').lower()=='true')])
        from std_msgs.msg import String
        self.guard_at=0.;self.guard_sub=self.node.create_subscription(String,'/tbot/guard_status',lambda m:setattr(self,'guard_at',time.monotonic()),10)
        self.String=String;self.authority_at=0.;self.authority_mode='stop';self.authority_pub=self.node.create_publisher(String,'/tbot/authority',10)
        self.lock=threading.RLock();self.map=None;self.scan=None;self.odom=None;self.battery=None;self.pose=None;self.imu_at=0.;self.motor_at=0.;self.motor={};self.goal=None;self.nav_state='idle';self.epoch=0;self.command=(0.,0.);self.command_at=0.;self.shutdown=False;self.map_load_state=None
        self.tf=Buffer();self.listener=TransformListener(self.tf,self.node)
        self.topics={};self.subscriptions={};self.publishers={}
        from sensor_msgs.msg import Imu
        self.types={'sensor_msgs/msg/LaserScan':(LaserScan,self.scan_cb),'nav_msgs/msg/OccupancyGrid':(OccupancyGrid,self.map_cb),'nav_msgs/msg/Odometry':(Odometry,self.odom_cb),'sensor_msgs/msg/BatteryState':(BatteryState,self.battery_cb),'sensor_msgs/msg/Imu':(Imu,self.imu_cb)}
        self.motor_sub=self.node.create_subscription(String,'/tbot/motor_status',self.motor_cb,10)
        self.sensor_qos=qos_profile_sensor_data;self.map_qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL,reliability=ReliabilityPolicy.RELIABLE)
        self.nav=ActionClient(self.node,NavigateToPose,os.environ.get('TBOT_NAV_ACTION','/navigate_to_pose'))
        self.path_client=ActionClient(self.node,ComputePathToPose,os.environ.get('TBOT_PATH_ACTION','/compute_path_to_pose'))
        self.node.create_timer(.05,self.tick);self.node.create_timer(2.,self.discover)
        self.thread=threading.Thread(target=rclpy.spin,args=(self.node,),daemon=True);self.thread.start()
    def discover(self):
        names=self.node.get_topic_names_and_types()
        with self.lock:
            for typ,(cls,cb) in self.types.items():
                label={'sensor_msgs/msg/LaserScan':'scan','nav_msgs/msg/OccupancyGrid':'map','nav_msgs/msg/Odometry':'odom','sensor_msgs/msg/BatteryState':'battery','sensor_msgs/msg/Imu':'imu'}[typ]
                candidates=[n for n,ts in names if typ in ts]
                configured=os.environ.get('TBOT_'+label.upper()+'_TOPIC');preferred=configured or '/'+{'battery':'battery_state'}.get(label,label)
                topic=preferred if preferred in candidates else (candidates[0] if not configured and len(candidates)==1 else None)
                if topic and label not in self.subscriptions:
                    self.subscriptions[label]=self.node.create_subscription(cls,topic,cb,self.map_qos if label=='map' else self.sensor_qos);self.topics[label]={'name':topic,'type':typ}
                elif not topic:self.topics[label]={'candidates':candidates,'error':'missing or ambiguous topic; configure TBOT_'+label.upper()+'_TOPIC'}
            # Manual and Nav2 commands are routed through the downstream guard.
            if not self.publishers:
                self.publishers['twist']=self.node.create_publisher(self.Twist,'/tbot/manual_velocity',10)
    def map_cb(self,m):
        if m.info.width*m.info.height>2000000:return
        with self.lock:self.map={'width':m.info.width,'height':m.info.height,'resolution':m.info.resolution,'origin':{'x':m.info.origin.position.x,'y':m.info.origin.position.y,'yaw':yaw(m.info.origin.orientation)},'data':list(m.data),'frame':m.header.frame_id,'stamp':m.header.stamp.sec+m.header.stamp.nanosec/1e9,'received':time.monotonic()}
    def scan_cb(self,m):
        from rclpy.time import Time
        try:
            target=self.map['frame'] if self.map else 'odom'
            tr=self.tf.lookup_transform(target,m.header.frame_id,Time.from_msg(m.header.stamp)).transform
            angle=yaw(tr.rotation);pts=[]
            for i,d in enumerate(m.ranges):
                if math.isfinite(d) and m.range_min<=d<=m.range_max:
                    a=m.angle_min+i*m.angle_increment+angle;pts.append([tr.translation.x+d*math.cos(a),tr.translation.y+d*math.sin(a)])
            with self.lock:self.scan={'points':pts,'frame':target,'stamp':m.header.stamp.sec+m.header.stamp.nanosec/1e9,'received':time.monotonic(),'error':None}
        except Exception as e:
            with self.lock:self.scan={'points':[],'received':0,'error':'Scan transform unavailable: '+str(e)[:180]}
    def odom_cb(self,m):
        with self.lock:self.odom={'x':m.pose.pose.position.x,'y':m.pose.pose.position.y,'yaw':yaw(m.pose.pose.orientation),'linear':m.twist.twist.linear.x,'angular':m.twist.twist.angular.z,'received':time.monotonic()}
    def battery_cb(self,m):
        with self.lock:self.battery={'voltage':m.voltage if math.isfinite(m.voltage) else None,'percent':m.percentage*100 if math.isfinite(m.percentage) and m.percentage>=0 else None}
    def imu_cb(self,m):
        with self.lock:self.imu_at=time.monotonic()
    def motor_cb(self,m):
        import json
        with self.lock:
            self.motor_at=time.monotonic()
            try:self.motor=json.loads(m.data)
            except Exception:self.motor={'error':'Malformed motor telemetry'}
    def tick(self):
        from rclpy.time import Time
        with self.lock:
            authority=self.String();authority.data=self.authority_mode if time.monotonic()-self.authority_at<.3 else 'stop';self.authority_pub.publish(authority)
            if self.publishers:
                v,w=self.command if time.monotonic()-self.command_at<.3 else (0.,0.)
                m=self.Twist();m.linear.x=v;m.angular.z=w;self.publishers['twist'].publish(m)
            try:
                target=self.map['frame'] if self.map else 'odom';tr=self.tf.lookup_transform(target,os.environ.get('TBOT_BASE_FRAME','base_footprint'),Time()).transform
                stamp=self.tf.lookup_transform(target,os.environ.get('TBOT_BASE_FRAME','base_footprint'),Time()).header.stamp
                age=(self.node.get_clock().now().nanoseconds-(stamp.sec*10**9+stamp.nanosec))/1e9
                self.pose={'x':tr.translation.x,'y':tr.translation.y,'yaw':yaw(tr.rotation),'frame':target,'received':time.monotonic(),'transform_age':age}
            except Exception:self.pose=None
    def authorize(self,mode):
        with self.lock:self.authority_mode=mode;self.authority_at=time.monotonic()
    def drive(self,v,w):
        with self.lock:self.command=(v,w);self.command_at=time.monotonic()
    def stop(self):
        with self.lock:
            self.epoch+=1;self.drive(0.,0.)
            if self.goal:self.goal.cancel_goal_async()
            self.goal=None;self.nav_state='idle'
    def navigate(self,p):
        from geometry_msgs.msg import PoseStamped
        if not self.nav.server_is_ready():raise ValueError('Nav2 action server unavailable')
        if not self.pose or self.pose['frame']!='map' or abs(self.pose['transform_age'])>1:raise ValueError('Fresh map localization required')
        self.epoch+=1;epoch=self.epoch
        goal=self.NavigateToPose.Goal();goal.pose=PoseStamped();goal.pose.header.frame_id='map';goal.pose.header.stamp=self.node.get_clock().now().to_msg();goal.pose.pose.position.x=float(p['x']);goal.pose.pose.position.y=float(p['y']);goal.pose.pose.orientation.z=math.sin(p.get('yaw',0)/2);goal.pose.pose.orientation.w=math.cos(p.get('yaw',0)/2)
        self.nav_state='pending';f=self.nav.send_goal_async(goal)
        def accepted(f):
            with self.lock:
                try:
                    handle=f.result()
                    if epoch!=self.epoch:
                        if handle.accepted:handle.cancel_goal_async()
                        return
                    if not handle.accepted:self.nav_state='failed';return
                    self.goal=handle;self.nav_state='active';r=handle.get_result_async()
                    def done(r):
                        with self.lock:
                            if epoch==self.epoch:self.nav_state='succeeded' if r.result().status==4 else 'failed';self.goal=None
                    r.add_done_callback(done)
                except Exception:self.nav_state='failed'
        f.add_done_callback(accepted)
    def _wait(self,future,timeout=4.):
        deadline=time.monotonic()+timeout
        while not future.done() and time.monotonic()<deadline:time.sleep(.01)
        if not future.done():raise ValueError('Nav2 path request timed out')
        return future.result()
    def compute_route(self,points):
        from geometry_msgs.msg import PoseStamped
        if not self.path_client.server_is_ready():raise ValueError('Nav2 compute path action is unavailable')
        segments=[];start=None
        for index,p in enumerate(points):
            goal=self.ComputePathToPose.Goal();goal.goal=PoseStamped();goal.goal.header.frame_id='map';goal.goal.header.stamp=self.node.get_clock().now().to_msg();goal.goal.pose.position.x=float(p['x']);goal.goal.pose.position.y=float(p['y']);goal.goal.pose.orientation.z=math.sin(p.get('yaw',0)/2);goal.goal.pose.orientation.w=math.cos(p.get('yaw',0)/2)
            if start is not None:goal.use_start=True;goal.start=start
            handle=self._wait(self.path_client.send_goal_async(goal))
            if not handle.accepted:raise ValueError(f'Nav2 rejected path segment {index+1}')
            wrapped=self._wait(handle.get_result_async());poses=[]
            for item in wrapped.result.path.poses:
                q=item.pose.orientation;poses.append({'x':item.pose.position.x,'y':item.pose.position.y,'yaw':yaw(q)})
            if not poses:raise ValueError(f'Nav2 found no path for segment {index+1}')
            distance=sum(math.hypot(b['x']-a['x'],b['y']-a['y']) for a,b in zip(poses,poses[1:]))
            segments.append({'checkpoint':index+1,'poses':poses,'distance_m':round(distance,3),'snapped_goal':p})
            start=goal.goal
        return segments
    def load_map(self,path):
        from nav2_msgs.srv import LoadMap
        client=self.node.create_client(LoadMap,'/map_server/load_map')
        if not client.service_is_ready():
            self.node.destroy_client(client)
            raise ValueError('Map server unavailable. Stop SLAM and launch localization with scripts/tbot-localization.sh first.')
        req=LoadMap.Request();req.map_url=path;self.map_load_state='pending';future=client.call_async(req)
        def done(f):
            try:self.map_load_state='succeeded' if f.result().result==0 else 'failed'
            except Exception:self.map_load_state='failed'
            self.node.destroy_client(client)
        future.add_done_callback(done)
    def snapshot(self):
        with self.lock:
            now=time.monotonic()
            diagnostics={'lidar':{'healthy':bool(self.scan and now-self.scan.get('received',0)<.5),'age':now-self.scan.get('received',0) if self.scan else None},'imu':{'healthy':now-self.imu_at<.5,'age':now-self.imu_at if self.imu_at else None},'motor_bus':{'healthy':now-self.motor_at<.5,'telemetry':self.motor},'tf':{'healthy':bool(self.pose),'age':self.pose.get('transform_age') if self.pose else None},'odometry':{'healthy':bool(self.odom and now-self.odom['received']<.5)},'guard':{'healthy':now-self.guard_at<.5},'nav2':{'healthy':self.nav.server_is_ready(),'state':self.nav_state}}
            return {'map_load':self.map_load_state,'mode':'ros-hardware','pose':self.pose,'odom':self.odom,'scan':self.scan,'map':self.map,'battery':self.battery,'topics':self.topics.copy(),'nav':self.nav_state,'scan_age':now-self.scan['received'] if self.scan and self.scan['received'] else None,'odom_age':now-self.odom['received'] if self.odom else None,'guard_ready':now-self.guard_at<.5,'navigation_ready':self.nav.server_is_ready(),'imu_ready':now-self.imu_at<.5,'motor_ready':now-self.motor_at<.5,'diagnostics':diagnostics}
    def close(self):
        self.stop();self.rclpy.shutdown();self.thread.join(timeout=2)
