# Copyright 2021 The ProLoaF Authors. All Rights Reserved.
#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# ==============================================================================

# Config-maker can be used to modify or generate config files run either with the path to the config file or the station name
# to erase the old config file before writing a new one do "python3 config_maker.py --new <stationname> "

import json
import sys
import os

main_path = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(main_path)

par = {}
if __name__ == '__main__':
    #read configs

    CONFIG_PATH = sys.argv[2]
    STATION_PATH = sys.argv[2].split('config')[0]
    STATION_NAME = STATION_PATH.split('targets/')[1]

    if(sys.argv[1] == '--mod'):
        with open(sys.argv[2],'r') as input:
            par = json.load(input)

    #when modifying a config file only select the parameters that are changed or creat new ones, the other will stay the same
    elif(sys.argv[1] == '--new'):
        print(CONFIG_PATH + ' has been reset before writing')
        par = {}
    else:
        print('No option selected either use either --mod or --new (WARNING: This  erases the old config file)')

    # INSERT MODIFICATIONS FOR THE CONFIG HERE
    # ============================================
    par["data_path"] = './data/<FILE-NAME>.csv'
    par["output_path"] = './oracles/'
    par["exploration_path"] = './' + STATION_PATH + 'tuning.json'
    par["evaluation_path"] = './oracles/eval_<MODEL-NAME>/'
    par["log_path"] = './logs/'
    par["model_name"] = '<MODEL-NAME>'
    par["target_id"] = '<COLUMN-IDENTIFIER>'
    par["target_list"] = None
    par["start_date"] = None
    par["history_horizon"] = 24
    par["forecast_horizon"] = 24
    par["train_split"] = 0.7
    par["validation_split"] = 0.85
    par["core_net"] = 'torch.nn.GRU'
    par["optimizer_name"] = 'adam'
    par["exploration"] = True
    par["relu_leak"] = 0.1
    par["cuda_id"] = None
    par["feature_groups"] = [
        {
            "name": "main",
            "scaler": [
                "robust",
                15,
                85
            ],
            "features": [
                '<COLUMN-IDENTIFIER-1>'
            ]
        },
        {
            "name": "add",
            "scaler": [
                "minmax",
                -1.0,
                1.0
            ],
            "features": [
                '<COLUMN-IDENTIFIER-2>'
            ]
        },
        {
            "name": "aux",
            "scaler": None,
            "features": [
                '<COLUMN-IDENTIFIER-3>',
                '<COLUMN-IDENTIFIER-4>'
            ]
        }
    ]
    par["encoder_features"] = [
        '<COLUMN-IDENTIFIER-1>',
        '<COLUMN-IDENTIFIER-2>'
    ]
    par["decoder_features"] = [
        '<COLUMN-IDENTIFIER-3>',
        '<COLUMN-IDENTIFIER-4>'
    ]
    par["max_epochs"] = 1
    par["batch_size"] = 2
    par["learning_rate"] = 1.0e-04
    par["core_layers"] = 1
    par["rel_linear_hidden_size"] = 1.
    par["rel_core_hidden_size"] = 1.
    par["dropout_fc"] = .4
    par["dropout_core"] = .3
    par["best_loss"] = None
    par["best_score"] = None

    with open(CONFIG_PATH,'w') as output:
        json.dump(par, output, indent = 4)
