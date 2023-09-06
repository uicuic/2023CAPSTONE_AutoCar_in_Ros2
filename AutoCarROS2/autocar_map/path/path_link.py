#!/usr/bin/env python3

import os
import numpy as np
import pandas as pd

from rclpy.node import Node
from ament_index_python.packages import get_package_share_directory

file_path = os.path.join(get_package_share_directory('autocar_map'), 'data')


class Path(Node):
    def __init__(self, bf_, gf_, pf_, rf_):

        self.global_map_x = {}
        self.global_map_y = {}
        self.ax = {}
        self.ay = {}
        self.car_mode = []

        if bf_ is not None:
            df = pd.read_csv(bf_)
            base = len(df['Link'].unique())
            self.base_map = [{} for _ in range(base)]
            for link in df['Link'].unique():

                data = df[df['Link'] == link]

                self.base_map[link]['X-axis'] = data['X-axis'].tolist()
                self.base_map[link]['Y-axis'] = data['Y-axis'].tolist()

        if gf_ is not None:
            df = pd.read_csv(gf_)
            for link in df['Link'].unique():

                var_name = 'global_' + str(link)

                data   = df[df['Link'] == link]
                before = df[df['Link'] == link-1].iloc[-5:]
                after  = df[df['Link'] == link+1].iloc[:10]

                if link == 0: link_data = pd.concat([data, after])
                else: link_data = pd.concat([before, data, after])

                self.ax[var_name] = link_data['X-axis'].tolist()
                self.ay[var_name] = link_data['Y-axis'].tolist()

                self.global_map_x[var_name] = data['X-axis'].tolist()
                self.global_map_y[var_name] = data['Y-axis'].tolist()

            self.car_mode = ['global' for _ in range(link+1)]
            self.car_mode[-1] = 'finish'
            self.next_path = ['none' for _ in range(link+1)]

        if pf_ is not None:
            df = pd.read_csv(pf_)
            for link in df['Link'].unique():

                var_name = 'parking_' + str(link)

                data = df[df['Link'] == link]

                self.ax[var_name] = data['X-axis'].tolist()
                self.ay[var_name] = data['Y-axis'].tolist()

        if rf_ is not None:
            df = pd.read_csv(rf_)
            for link in df['Link'].unique():

                var_name = 'revpark_' + str(link)

                data = df[df['Link'] == link]

                self.ax[var_name] = data['X-axis'].tolist()
                self.ay[var_name] = data['Y-axis'].tolist()


def test_track():
    base_file = file_path + '/ST_base.csv'
    global_file = file_path + '/htech/htech_track.csv'
    parking_file = None
    revpark_file = None
    test_track = Path(base_file, global_file, parking_file, revpark_file)
    test_track.car_mode[1] = 'tunnel'
    test_track.car_mode[3] = 'delivery_A'
    test_track.car_mode[5] = 'dynamic'
    test_track.car_mode[6] = 'delivery_B'

    left = [3,4]
    right = [1,2]
    none = [0,5]
    for i in left:
        test_track.next_path[i] = 'left'
    for i in right:
        test_track.next_path[i] = 'right'
    for i in none:
        test_track.next_path[i] = 'none'

    return test_track


def boong():
    base_file = file_path + '/ST_base.csv'
    global_file = file_path + '/boong/boong_track.csv'
    parking_file = file_path + '/boong/boong_parking.csv'
    revpark_file = file_path + '/boong/boong_revpark.csv'
    boong = Path(base_file, global_file, parking_file, revpark_file)
    boong.car_mode[1] = 'revpark'
    boong.car_mode[3] = 'dynamic'
    boong.car_mode[5] = 'static'
    boong.car_mode[7] = 'delivery_A'
    boong.car_mode[8] = 'uturn'
    boong.car_mode[9] = 'delivery_B'
    boong.car_mode[11] = 'tunnel'
    # boong.car_mode[14] = 'revpark'

    left = [7,8,9,11]
    right = [3,5]
    none = [12,13,14]
    for i in left:
        boong.next_path[i] = 'left'
    for i in right:
        boong.next_path[i] = 'right'
    for i in none:
        boong.next_path[i] = 'none'

    return boong


def htech():
    base_file = file_path + '/ST_base.csv'
    global_file = file_path + '/htech/htech_track.csv'
    # global_file = file_path + '/htech/delivery.csv'
    parking_file = None
    revpark_file = None
    htech = Path(base_file, global_file, parking_file, revpark_file)
    htech.car_mode[1] = 'tunnel'
    # htech.car_mode[4] = 'tunnel'
    # htech.car_mode[0] = 'delivery_A'
    # htech.car_mode[3] = 'delivery_B'

    return htech

def revpark():
    base_file = file_path + '/ST_base.csv'
    global_file = file_path + '/htech/park_test.csv'
    parking_file = file_path + '/htech/parking.csv'
    revpark_file = file_path + '/htech/revpark.csv'
    revpark = Path(base_file, global_file, parking_file, revpark_file)
    revpark.car_mode[0] = 'parking'

    return revpark

def uturn():
    base_file = file_path + '/ST_base.csv'
    global_file = file_path + '/htech/uturn.csv'
    parking_file = None
    revpark_file = None
    uturn = Path(base_file, global_file, parking_file, revpark_file)
    uturn.car_mode[1] = 'uturn'
    uturn.car_mode[2] = 'tollgate'

    return uturn

def kcity():
    base_file = file_path + '/KC_base.csv'
    global_file = file_path + '/kcity/track.csv'
    parking_file = file_path + '/kcity/track_lane.csv'
    revpark_file = file_path + '/kcity/revpark.csv'
    kcity = Path(base_file, global_file, parking_file, revpark_file)
    kcity.car_mode[1] = 'delivery_A'
    kcity.car_mode[2] = 'static'
    kcity.car_mode[5] = 'static'
    kcity.car_mode[6] = 'delivery_B'
    kcity.car_mode[12] = 'revpark'

    straight = [4,5,9,10]
    left = [3,6,7]
    right = [2,8]
    for i in straight:
        kcity.next_path[i] = 'straight'
    for i in left:
        kcity.next_path[i] = 'left'
    for i in right:
        kcity.next_path[i] = 'right'

    return kcity

def qualifier():
    base_file = file_path + '/KC_base.csv'
    global_file = file_path + '/kcity/qualifier.csv'
    parking_file = None
    revpark_file = None
    qualifier = Path(base_file, global_file, parking_file, revpark_file)
    qualifier.car_mode[1] = 'uturn'
    qualifier.car_mode[2] = 'tollgate'
    qualifier.car_mode[3] = 'tunnel'

    return qualifier

use_map = uturn()
start_index = 0
