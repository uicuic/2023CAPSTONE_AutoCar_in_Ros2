#!/usr/bin/env python3

import os
import time
import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline

import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.callback_groups import ReentrantCallbackGroup
from ament_index_python.packages import get_package_share_directory

from nav_msgs.msg import Path
from std_msgs.msg import Float64MultiArray
from autocar_msgs.msg import Path2D, State2D, ObjectArray, LinkArray, Obstacle
from geometry_msgs.msg import Pose2D, PoseStamped
from visualization_msgs.msg import Marker, MarkerArray

from autocar_nav.quaternion import yaw_to_quaternion
from autocar_nav.hybrid_a_star import hybrid_a_star
from autocar_nav.separation_axis_theorem import separating_axis_theorem, get_vertice_rect


class LocalPathPlanner(Node):

    def __init__(self):
        super().__init__('local_planner')

        # Initialise publishers
        self.local_planner_pub = self.create_publisher(Path2D, '/autocar/path', 10)
        self.path_viz_pub = self.create_publisher(Path, '/autocar/viz_path', 10)
        self.obs_recog_pub = self.create_publisher(Obstacle, '/autocar/obs_recog', 10)
        self.center_viz_pub = self.create_publisher(MarkerArray, '/rviz/pathlane', 10)
        self.static_obs_pub = self.create_publisher(MarkerArray, '/rviz/static_obs', 10)

        # Initialise subscribers
        self.goals_sub = self.create_subscription(Path2D, '/autocar/goals', self.goals_cb, 10)
        self.lanes_sub = self.create_subscription(Path2D, '/autocar/tunnel_lane', self.lanes_cb, 10)
        self.localisation_sub = self.create_subscription(State2D, '/autocar/state2D', self.vehicle_state_cb, 10, callback_group=ReentrantCallbackGroup())
        # self.offset_sub = self.create_subscription(Float64MultiArray, '/autocar/tunnel_offset', self.offset_cb, 10)
        self.obstacle_sub = self.create_subscription(ObjectArray, '/obstacles', self.obstacle_cb, 10)
        self.mode_sub = self.create_subscription(LinkArray, '/autocar/mode', self.mode_cb, 10)

        # Load parameters
        try:
            self.declare_parameters(
                namespace='',
                parameters=[
                    ('update_frequency', None),
                    ('frame_id', None),
                    ('car_width', None),
                    ('centreofgravity_to_frontaxle', None)
                ]
            )

            self.frequency = float(self.get_parameter("update_frequency").value)
            self.car_width = float(self.get_parameter("car_width").value)
            self.cg2frontaxle = float(self.get_parameter("centreofgravity_to_frontaxle").value)

        except:
            raise Exception("Missing ROS parameters. Check the configuration file.")

        ##################### 회피주행시 넘으면 안되는 선의 위치정보 ##########################
        file_path = os.path.join(get_package_share_directory('autocar_map'), 'data')
        df = pd.read_csv(file_path + '/kcity/track_lane.csv')
        self.center_x = df['x'].tolist()
        self.center_y = df['y'].tolist()
        # self.center_x = [i - 1.2 for i in self.center_x]
        # self.center_y = [i + 1.0 for i in self.center_y]
        self.center_yaw = df['yaw'].tolist()
        self.offset_x = 0.0
        self.offset_y = 0.0
        ###############################################################################
        self.ds = 1 / self.frequency

        self.ax = []
        self.ay = []
        self.obstacles = []

        self.target_path = Path2D()
        self.viz_path = Path()

        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.start = time.time()
        self.mode = 'global'
        self.GtoL = 1.29 # gps to lidar distance
        self.L = 1.6 #1.04/2+1.6/2 # 차량 길이
        self.W = 1.45 # 차량 폭
        self.p_L = 5.0 # 차선 길이
        self.p_W = 0.1 # 차선 폭
        self.obstacle_detected = False
        self.obstacle_info = 'None'
        self.obstacle_dist = float(1e3)
        self.dist_thresh = 6 # 정적 및 동적 판단 기준 : 6m
        self.queue = 0
        self.prev_dist = None
        self.is_fail = False
        self.static_obstacles = []
        self.path_lane = []

    def goals_cb(self, msg):
        '''
        Callback function to recieve immediate goals from global planner in global frame
        '''
        self.ax = []
        self.ay = []
        for i in range(len(msg.poses)):
            px = msg.poses[i].x
            py = msg.poses[i].y
            self.ax.append(px)
            self.ay.append(py)

    def lanes_cb(self, msg):
        if self.mode == 'tunnel':
            self.center_x = []
            self.center_y = []
            self.center_yaw = []
            for i in range(len(msg.poses)):
                px = msg.poses[i].x
                py = msg.poses[i].y
                pyaw = msg.poses[i].theta
                self.center_x.append(px)
                self.center_y.append(py)
                self.center_yaw.append(pyaw)

    def vehicle_state_cb(self, msg):
        '''
        Callback function to recieve vehicle state information from localization in global frame
        '''
        self.x = msg.pose.x
        self.y = msg.pose.y
        self.yaw = msg.pose.theta
        self.find_path()

    def mode_cb(self, msg):
        self.waypoint = msg.closest_wp
        self.mode = msg.mode
        self.traffic_stop_wp = msg.traffic_stop_wp
        self.parking_stop_wp = msg.parking_stop_wp
        self.direction = msg.direction
        self.next_path = msg.next_path

    def offset_cb(self, msg):
        self.offset_x, self.offset_y = msg.data

    def obstacle_cb(self, msg):
        self.obstacles = [(o.x, o.y, o.yaw, o.length, o.width) for o in msg.object_list]
        self.path_lane = []
        # 차선정보 넣기
        if self.mode in ['tunnel', 'static']:
            for i in range(len(self.center_x)):
                # length는 waypoint 간격만큼, width는 차선의 폭 (가능하면 최대한 작게)
                self.path_lane.append((self.center_x[i] - self.offset_x, self.center_y[i] - self.offset_y, self.center_yaw[i], self.p_L, self.p_W))

        self.viz_path_lane()

    def viz_path_lane(self):

        marray2 = MarkerArray()
        static_obstacle = self.static_obstacles
        i = 0
        for obs in static_obstacle:
            m = Marker()
            m.header.frame_id = "/map"
            m.header.stamp = self.get_clock().now().to_msg()
            m.type = m.CUBE
            m.id = i
            i += 1

            m.pose.position.x = obs[0]
            m.pose.position.y = obs[1]
            m.pose.position.z = 0.75
            quat = yaw_to_quaternion(obs[2])
            m.pose.orientation.x = quat.x
            m.pose.orientation.y = quat.y
            m.pose.orientation.z = quat.z
            m.pose.orientation.w = quat.w

            m.scale.x = obs[3]
            m.scale.y = obs[4]
            m.scale.z = 1.645

            m.color.r = 121 / 255.0
            m.color.g = 12 / 255.0
            m.color.b = 102 / 255.0
            m.color.a = 0.97

            m.lifetime = Duration(nanoseconds= 100000000).to_msg()
            marray2.markers.append(m)

        marray = MarkerArray()
        for i in range(len(self.center_x)):
            m = Marker()
            m.header.frame_id = "/map"
            m.header.stamp = self.get_clock().now().to_msg()
            m.type = m.CUBE
            m.id = i

            m.pose.position.x = self.center_x[i] - self.offset_x
            m.pose.position.y = self.center_y[i] - self.offset_y
            m.pose.position.z = 0.75
            quat = yaw_to_quaternion(self.center_yaw[i])
            m.pose.orientation.x = quat.x
            m.pose.orientation.y = quat.y
            m.pose.orientation.z = quat.z
            m.pose.orientation.w = quat.w

            m.scale.x = self.p_L
            m.scale.y = self.p_W
            m.scale.z = 1.645

            m.color.r = 121 / 255.0
            m.color.g = 204 / 255.0
            m.color.b = 102 / 255.0
            m.color.a = 0.97

            m.lifetime = Duration(nanoseconds=150000000).to_msg()
            marray.markers.append(m)

        self.static_obs_pub.publish(marray2)
        self.center_viz_pub.publish(marray)

    def determine_path(self, cx, cy, cyaw):
        self.obstacle_detected = False
        self.obstacle_info = 'None'

        obstacle_colliding = []
        for obs in self.obstacles:
            for i in range(0,len(cyaw),15):
                car_vertices = get_vertice_rect((cx[i],cy[i],cyaw[i], 1.6, 1.7))
                obstacle_vertices = get_vertice_rect(obs)
                is_collide = separating_axis_theorem(car_vertices, obstacle_vertices)

                if is_collide:
                    obstacle_colliding.append(obs)
                    self.update_static_obstacle(obs)
                    # self.static_obstacles.append(obs)
                    break

        # 모드에 따라 reroute할것인지 급정거 할 것인지 설정
        if len(obstacle_colliding) != 0:
            self.queue = 0
            o = obstacle_colliding[0]
            if self.mode in ['static', 'tunnel']:
                cx, cy, cyaw = self.collision_reroute(cx, cy, cyaw, obstacle_colliding)
                self.obstacle_detected = True
                self.obstacle_dist = np.sqrt((self.x - o[0])**2 + (self.y - o[1])**2)

            elif self.mode == 'dynamic':
                self.obstacle_detected = True
                self.obstacle_dist = np.sqrt((self.x - o[0])**2 + (self.y - o[1])**2)

            elif self.mode == 'uturn':
                self.obstacle_detected = True
                self.obstacle_info = 'rubber_cone'
                self.obstacle_dist = np.sqrt((self.x - o[0])**2 + (self.y - o[1])**2)

        ## 최근에 장애물 검출이 안된다면 prev_dist를 None으로 변경
        else:
            self.queue += 1
            if self.queue >= 5:
                self.queue = 5
                self.prev_dist = None

        return cx, cy, cyaw

    def collision_reroute(self, cx, cy, cyaw, obstacle_colliding):
        step = 55
        # step_region = 15
        obs_first = obstacle_colliding[-1]
        obs_end = obstacle_colliding[0]

        # dx_f = [obs_first[0] - icx for icx in cx] # Find the x-axis of the front axle relative to the path
        # dy_f = [obs_first[1] - icy for icy in cy] # Find the y-axis of the front axle relative to the path

        dx_e = [obs_end[0] - icx for icx in cx] # Find the x-axis of the front axle relative to the path
        dy_e = [obs_end[1] - icy for icy in cy] # Find the y-axis of the front axle relative to the path

        # d_f = np.hypot(dx_f, dy_f) # Find the distance from the front axle to the path
        # target_idx_f = np.argmin(d_f)

        d_e = np.hypot(dx_e, dy_e) # Find the distance from the front axle to the path
        target_idx_e = np.argmin(d_e)

        self.get_logger().info('obstacle detected!!  target idx : %d' %(target_idx_e))

        ## 가까운 장애물과의 거리
        dist = np.sqrt((self.x - obs_end[0])**2 + (self.y - obs_end[1])**2)
        ## 장애물거리가 가깝고 최근에 장애물이 없었다면 동적 장애물로 판단
        if dist <= self.dist_thresh:
            if self.prev_dist is None:
                self.obstacle_info = 'dynamic'
            else:
                self.obstacle_info = 'static'
                self.prev_dist = dist
        else:
            self.obstacle_info = 'static'
            self.prev_dist = dist

        # if target_idx_e + (step+10) >= len(cyaw) or target_idx_f - (step+10) <= 0: # 10 대신 step_region
            # return cx, cy, cyaw
        if target_idx_e + (step+5) >= len(cyaw) or target_idx_e - (step+5) <= 0: # 10 대신 step_region
            return cx, cy, cyaw

        # Points to leave path
        start_x= cx[target_idx_e - step]
        start_y= cy[target_idx_e - step]
        start_yaw= cyaw[target_idx_e - step]
        start = (start_x, start_y, np.rad2deg(start_yaw))

        end_x = cx[target_idx_e + step]
        end_y = cy[target_idx_e + step]
        end_yaw = cyaw[target_idx_e + step]
        end = (end_x, end_y,  np.rad2deg(end_yaw))

        region1_x = cx[target_idx_e] - 15
        region1_y = cy[target_idx_e] - 15
        region2_x = cx[target_idx_e] + 15
        region2_y = cy[target_idx_e] + 15
        
        static_obstacle = self.obstacles + self.static_obstacles + self.path_lane

        hy_a_star = hybrid_a_star(region1_x, region2_x,
                                  region1_y, region2_y,
                                  obstacle = static_obstacle,
                                  resolution = 1.0,
                                  length = self.L, width = self.W)
        reroute_path = hy_a_star.find_path(start, end)

        if reroute_path is None:
          self.get_logger().info("시간 초과, 회피경로 재탐색")
          self.is_fail = True
          return cx,cy,cyaw

        # resolution은 항상 self.W 보다 작아야함.

        rcx_ = []
        rcy_ = []
        rcyaw_ = []
        for i in range(len(reroute_path)):
            rcx_.append(reroute_path[i][0])
            rcy_.append(reroute_path[i][1])
            rcyaw_.append(reroute_path[i][2])
        # rcx_.append(cx[(target_idx_e + step + 1)])
        # rcy_.append(cy[(target_idx_e + step + 1)])

        cx_ = CubicSpline(range(len(rcx_)), rcx_)
        cy_ = CubicSpline(range(len(rcy_)), rcy_)
        dx = cx_(np.arange(0, len(rcx_) - 1, 0.2))
        dy = cy_(np.arange(0, len(rcy_) - 1, 0.2))
        rcyaw = np.arctan2(dy[1:] - dy[:-1], dx[1:] - dx[:-1])
        rcx = dx[:-1]
        rcy = dy[:-1]

        # stiching to form new path
        cx   = np.concatenate(( cx[0 : target_idx_e - step+1], rcx, cx[(target_idx_e + step+1) : ]))
        cy   = np.concatenate(( cy[0 : target_idx_e - step+1], rcy, cy[(target_idx_e + step+1) : ]))
        cyaw   = np.concatenate(( cyaw[0 : target_idx_e - step+1], rcyaw, cyaw[(target_idx_e + step+1) : ]))

        # print('Generated dev path')
        return cx, cy, cyaw

    def update_static_obstacle(self, obs):
        min_dist = 1000000

        if len(self.static_obstacles) == 0:
            self.static_obstacles.append(obs)
        else:
            for static_obs in self.static_obstacles:
                # self.get_logger().info(f'{obs[1]}, {static_obs[0]}')
                dist = np.sqrt((obs[0] - static_obs[0])**2 + (obs[1] - static_obs[1])**2)
                if dist < min_dist:
                    min_dist = dist

            if min_dist > 1.0:
                self.static_obstacles.append(obs)

    def find_path(self):
        self.is_fail = False
        if len(self.ax) < 2:
            return
        cx_ = CubicSpline(range(len(self.ax)), self.ax)
        cy_ = CubicSpline(range(len(self.ay)), self.ay)
        dx = cx_(np.arange(0, len(self.ax) - 1, 0.1))
        dy = cy_(np.arange(0, len(self.ay) - 1, 0.1))
        cyaw = np.arctan2(dy[1:] - dy[:-1], dx[1:] - dx[:-1])
        cx = dx[:-1]
        cy = dy[:-1]

        if self.mode in ['dynamic', 'static', 'tunnel', 'uturn']:
            cx, cy, cyaw = self.determine_path(cx, cy, cyaw)
        else:
            self.static_obstacles = []
            
        if self.is_fail == True:
            return

        obs = Obstacle()
        obs.detected = self.obstacle_detected
        obs.obstacle = self.obstacle_info
        obs.distance = self.obstacle_dist
        self.obs_recog_pub.publish(obs)

        path_length = min(len(cx), len(cy), len(cyaw))

        self.target_path = Path2D()
        self.viz_path = Path()

        self.viz_path.header.frame_id = "map"
        self.viz_path.header.stamp = self.get_clock().now().to_msg()

        for n in range(0, path_length):
            # Appending to Target Path
            npose = Pose2D()
            npose.x = cx[n]
            npose.y = cy[n]
            npose.theta = cyaw[n]
            self.target_path.poses.append(npose)

            # Appending to Visualization Path
            vpose = PoseStamped()
            vpose.header.frame_id = "map"
            vpose.header.stamp = self.get_clock().now().to_msg()
            vpose.pose.position.x = cx[n]
            vpose.pose.position.y = cy[n]
            vpose.pose.position.z = 0.0
            vpose.pose.orientation = yaw_to_quaternion(np.pi * 0.5 - cyaw[n])
            self.viz_path.poses.append(vpose)

        self.local_planner_pub.publish(self.target_path)
        self.path_viz_pub.publish(self.viz_path)


def main(args=None):
    rclpy.init(args=args)

    try:
        local_planner = LocalPathPlanner()
        rclpy.spin(local_planner)

    finally:
        local_planner.destroy_node()
        rclpy.shutdown()

if __name__=="__main__":
    main()