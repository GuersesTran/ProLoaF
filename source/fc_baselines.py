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

# -*- coding: utf-8 -*-
"""
Baseline Functions
"""
import os
import sys
import argparse

import pandas as pd
import torch
import matplotlib.pyplot as plt
import contextlib
import numpy as np

torch.set_printoptions(linewidth=120) # Display option for output
torch.set_grad_enabled(True)

MAIN_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(MAIN_PATH)

import plf_util.datatuner as dt
import plf_util.eval_metrics as metrics
import plf_util.baseline_forecasts as baselines

from plf_util.config_util import read_config, parse_with_loss

class flag_and_store(argparse._StoreAction):
    def __init__(self, option_strings, dest, dest_const, const,
                 nargs = 0, **kwargs):
        self.dest_const = dest_const
        if isinstance(const, list):
            self.val = const[1:]
            self.flag = const[0]
        else:
            self.val = []
            self.flag = const
        if isinstance(nargs, int):
            nargs_store = nargs+len(self.val)
        else:
            nargs_store = nargs
        super(flag_and_store, self).__init__(option_strings, dest, const = None,
                                             nargs = nargs_store, **kwargs)
        self.nargs = nargs

    def __call__(self, parser, namespace, values, option_strings = None):
        setattr(namespace, self.dest_const,self.flag)
        if isinstance(values,list):
            self.val.extend(values)
        elif values is not None:
            self.val.append(values)

        if len(self.val) == 1:
            self.val = self.val[0]
        super().__call__(parser, namespace, self.val, option_strings)

def eval_baseline(mean_forecast, df_target, upper_PI, lower_PI, load_limit, hours, baseline_method='baseline',
                  forecast_horizon=1):
    mse, rmse, mase, rae, mae, sharpness, coverage, mis = \
        baselines.eval_forecast(mean_forecast, df_target,
                                upper_PI, lower_PI,total=True)
    rmse_horizon, sharpness_horizon, coverage_horizon, mis_horizon = \
        baselines.eval_forecast(mean_forecast, df_target,
                                upper_PI, lower_PI, total=False)
    print(metrics.results_table(baseline_method + '_' + ARGS.station, mse.numpy(), rmse.numpy(), mase.numpy(),
                                rae.numpy(), mae.numpy(), sharpness.numpy(), coverage.numpy(), mis.numpy(),
                                save_to_disc=OUTDIR))
    # plot metrics
    metrics.plot_metrics(rmse_horizon.numpy(), sharpness_horizon.numpy(), coverage_horizon.numpy(),
                         mis_horizon.numpy(), OUTDIR, baseline_method)

    testhours = [0, 12, 24, 48, 100, 112]

    for i in testhours:
        if i + forecast_horizon < len(mean_forecast):
            targets = df_target[i]
            expected_values = mean_forecast[i]
            y_pred_upper = upper_PI[i]
            y_pred_lower = lower_PI[i]
            metrics.evaluate_hours(targets, expected_values, y_pred_upper, y_pred_lower, i,
                                   OUTDIR + 'baseline_' + baseline_method+'_', load_limit, hours[i:i+forecast_horizon])

def main(infile, target_id):
    sarimax_model = None
    # Read load data
    df = pd.read_csv(infile, sep=';',index_col=0)
    dt.fill_if_missing(df)
    time_delta = pd.to_datetime(df.index[1]) - pd.to_datetime(df.index[0])
    timestamps = pd.date_range(start=pd.to_datetime(df.index, dayfirst=DAY_FIRST)[0],
                          end=pd.to_datetime(df.index, dayfirst=DAY_FIRST)[-1],
                          freq=str(time_delta.seconds / 3600) + 'H')
    df.index = timestamps
    if ('col_list' in PAR):
        if(PAR['col_list'] != None):
            df[target_id] = df[PAR['col_list']].sum(axis=1)

    try:
        target = PAR['target_id']
        enc_features = PAR['encoder_features']
        dec_features = PAR['decoder_features']

        if SCALE_DATA:
            df, _ = dt.scale_all(df, **PAR)

        df_train = df.iloc[:int(PAR['validation_split'] * len(df))]
        df_val = df.iloc[int(PAR['validation_split'] * len(df)):]

        x_train = dt.extract(df_train.iloc[:-PAR['forecast_horizon']], PAR['forecast_horizon'])
        x_val = dt.extract(df_val.iloc[:-PAR['forecast_horizon']], PAR['forecast_horizon'])
        y_train = dt.extract(df_train.iloc[PAR['forecast_horizon']:], PAR['forecast_horizon'])
        y_val = dt.extract(df_val.iloc[PAR['forecast_horizon']:], PAR['forecast_horizon'])

        target_column = df.columns.get_loc(target)

        x_train_1D = x_train[:,:,target_column]
        x_val_1D = x_val[:,:,target_column]
        y_train_1D = y_train[:,:,target_column]

        mean_forecast = []
        upper_PI = []
        lower_PI = []
        baseline_method = []

        if EXOG:
            df_exog_train = df_train[enc_features+dec_features]
            df_exog_val = df_val[enc_features+dec_features]
        else:# empty exogenous features, to ignore them in following baseline models, can be removed actually...
            df_exog_train = None
            df_exog_val = None

        if 'sarima' in CALC_BASELINES or 'sarimax' in CALC_BASELINES:
            ###############################ARIMA####################################
            sarimax_model = baselines.load_baseline(OUTDIR,name='ARIMA')
            if sarimax_model == None or not APPLY_EXISTING_MODEL:
                sarimax_model,_,_ = baselines.auto_sarimax_wrapper(endog=df_train[target],
                                                                   exog=None,
                                                                   order=ORDER, seasonal_order=sORDER, seasonal = False,
                                                                   trend='n', grid_search = PAR['exploration'])
            else: print('Loaded existing fitted ARIMA model from ',OUTDIR)
            ARIMA_expected_values, ARIMA_y_pred_upper, ARIMA_y_pred_lower = \
                baselines.make_forecasts(df_train[target], df_val[target],None,
                                         None, sarimax_model,
                                         PAR['forecast_horizon'], limit_steps=NUM_PRED, pi_alpha = ALPHA, online=True)
            mean_forecast.append(ARIMA_expected_values)
            upper_PI.append(ARIMA_y_pred_upper)
            lower_PI.append(ARIMA_y_pred_lower)
            baseline_method.append('ARIMA')
            baselines.save_baseline(OUTDIR,sarimax_model,name='ARIMA',  save_predictions=False)
            ###############################SARIMA####################################
            sarimax_model = baselines.load_baseline(OUTDIR,name='SARIMA')
            if sarimax_model == None or not APPLY_EXISTING_MODEL:
                sarimax_model,_,_ = baselines.auto_sarimax_wrapper(endog=df_train[target],
                                                                   exog=None,
                                                                   order=ORDER, seasonal_order=sORDER, seasonal = True,
                                                                   trend='n', grid_search = PAR['exploration'])
            else: print('Loaded existing fitted SARIMA model from ',OUTDIR)
            SARIMA_expected_values, SARIMA_y_pred_upper, SARIMA_y_pred_lower = \
                baselines.make_forecasts(df_train[target], df_val[target],None,
                                         None, sarimax_model,
                                         PAR['forecast_horizon'], limit_steps=NUM_PRED, pi_alpha = ALPHA, online=True)
            mean_forecast.append(SARIMA_expected_values)
            upper_PI.append(SARIMA_y_pred_upper)
            lower_PI.append(SARIMA_y_pred_lower)
            baseline_method.append('SARIMA')
            baselines.save_baseline(OUTDIR, sarimax_model, name='SARIMA', save_predictions=False)
            ###############################ARIMAX####################################
            sarimax_model = baselines.load_baseline(OUTDIR, name='ARIMAX')
            if sarimax_model == None or not APPLY_EXISTING_MODEL:
                sarimax_model,_,_ = baselines.auto_sarimax_wrapper(endog=df_train[target],
                                                                   exog=df_exog_train,
                                                                   order=ORDER, seasonal_order=sORDER, seasonal = False,
                                                                   trend='n', grid_search = PAR['exploration'])
            else: print('Loaded existing fitted ARIMAX model from ',OUTDIR)
            ARIMAX_expected_values, ARIMAX_y_pred_upper, ARIMAX_y_pred_lower = \
                baselines.make_forecasts(df_train[target], df_val[target],df_exog_train,
                                         df_exog_val, sarimax_model,
                                         PAR['forecast_horizon'], limit_steps=NUM_PRED, pi_alpha = ALPHA, online=True)
            mean_forecast.append(ARIMAX_expected_values)
            upper_PI.append(ARIMAX_y_pred_upper)
            lower_PI.append(ARIMAX_y_pred_lower)
            baseline_method.append('ARIMAX')
            baselines.save_baseline(OUTDIR, sarimax_model, name='ARIMAX', save_predictions=False)
            ###############################SARIMAX###################################
            sarimax_model = baselines.load_baseline(OUTDIR, name='SARIMAX')
            if sarimax_model == None or not APPLY_EXISTING_MODEL:
                sarimax_model,_,_ = baselines.auto_sarimax_wrapper(endog=df_train[target],
                                                                   exog=df_exog_train,
                                                                   order=ORDER, seasonal_order=sORDER, seasonal = True,
                                                                   trend='n', grid_search = PAR['exploration'])
            else: print('Loaded existing fitted SARIMAX model from ',OUTDIR)
            SARIMAX_expected_values, SARIMAX_y_pred_upper, SARIMAX_y_pred_lower = \
                baselines.make_forecasts(df_train[target], df_val[target],df_exog_train,
                                         df_exog_val, sarimax_model,
                                         PAR['forecast_horizon'], limit_steps=NUM_PRED, pi_alpha = ALPHA, online=True)
            mean_forecast.append(SARIMAX_expected_values)
            upper_PI.append(SARIMAX_y_pred_upper)
            lower_PI.append(SARIMAX_y_pred_lower)
            baseline_method.append('SARIMAX')
            baselines.save_baseline(OUTDIR, sarimax_model, name='SARIMAX', save_predictions=False)

        if 'simple-naive' in CALC_BASELINES:
        #Naïve
            naive_expected_values, naive_y_pred_upper, naive_y_pred_lower  = \
                baselines.persist_forecast(x_train_1D, x_val_1D, y_train_1D, PAR['forecast_horizon'], alpha=ALPHA)
            mean_forecast.append(naive_expected_values)
            upper_PI.append(naive_y_pred_upper)
            lower_PI.append(naive_y_pred_lower)
            baseline_method.append('simple-naive')

        if 'seasonal-naive' in CALC_BASELINES:
        #SNaïve
            s_naive_expected_values, s_naive_y_pred_upper, s_naive_y_pred_lower = \
                baselines.seasonal_forecast(x_train_1D, x_val_1D, y_train_1D, PAR['forecast_horizon'],
                                            seasonality=SEASONALITY, alpha=ALPHA)
            mean_forecast.append(s_naive_expected_values)
            upper_PI.append(s_naive_y_pred_upper)
            lower_PI.append(s_naive_y_pred_lower)
            baseline_method.append('seasonal-naive')

        if 'naive-stl' in CALC_BASELINES:
        #decomposition(+ any#model)
            sd_naive_expected_values, sd_naive_y_pred_upper, sd_naive_y_pred_lower  = \
                baselines.persist_forecast(x_train_1D, x_val_1D, y_train_1D, PAR['forecast_horizon'],
                                            decomposed=True, alpha=ALPHA)
            mean_forecast.append(sd_naive_expected_values)
            upper_PI.append(sd_naive_y_pred_upper)
            lower_PI.append(sd_naive_y_pred_lower)
            baseline_method.append('naive-stl')

        if 'ets' in CALC_BASELINES:
        # Exponential smoothing
            #with contextlib.redirect_stdout(None):
            ets_expected_values, ets_y_pred_upper, ets_y_pred_lower = \
                baselines.exp_smoothing(df_train[target], df_val[target], PAR['forecast_horizon'],
                                        limit_steps=NUM_PRED, online = True)#rename update to online
            mean_forecast.append(ets_expected_values)
            upper_PI.append(ets_y_pred_upper)
            lower_PI.append(ets_y_pred_lower)
            baseline_method.append('ets')

        if 'sarima' in CALC_BASELINES and 'garch' in CALC_BASELINES or \
                'sarimax' in CALC_BASELINES and 'garch' in CALC_BASELINES:
        #GARCH
        # e.g. Garch (p'=1,q'=1) has the Arch order 1(past residuals squared) and the Garch order 1(past variance:=sigma^2)
        # in comparison, ARMA (p=1,p=1) has AR order 1(past observations) and MA order 1(past residuals)
        #--> this is why we just use the computed mean by sarimax (called SARIMAX_expected_values here)

        # why not more than one Garch component? when forecasting market returns e.g.,
        # all the effects of conditional variance of t-2 are contained in the conditional variance t-1
        # p: is arch order and q ist garch order
            # arch component is nearly equivalent to the MA component of SARIMAX_expected_values here --> p' = q
            # garch component is nearly equivalent to the AR component of SARIMAX -->q'= p
            p_garch = sarimax_model.model_orders['ma']
            q_garch =sarimax_model.model_orders['ar']
            GARCH_expected_values = SARIMAX_expected_values
            GARCH_y_pred_upper, GARCH_y_pred_lower = \
                baselines.GARCH_predictioninterval(df_train[target], df_val[target], PAR['forecast_horizon'],
                                                   mean_forecast=GARCH_expected_values,
                                                   limit_steps=NUM_PRED, alpha=ALPHA)# p=p_garch,q=q_garch,
            mean_forecast.append(GARCH_expected_values)
            upper_PI.append(GARCH_y_pred_upper)
            lower_PI.append(GARCH_y_pred_lower)
            baseline_method.append('ets')

        #further options possible, checkout this blog article: https://towardsdatascience.com/an-overview-of-time-series-forecasting-models-a2fa7a358fcb
        #Dynamic linear models
        #TBATS
        #Prophet
        #----------
        #randomforest --already existing in notebook
        #gradboosting --already existing in old project

        #==========================Evaluate ALL==========================
        for i, mean_forecast in enumerate(mean_forecast):
            if len(mean_forecast) - PAR['forecast_horizon'] == len(y_val):
                eval_baseline(mean_forecast[PAR['forecast_horizon']:,:], y_val[:,:,target_column],
                              upper_PI[i][PAR['forecast_horizon']:,:], lower_PI[i][PAR['forecast_horizon']:,:],
                              PAR['cap_limit'],
                              hours = pd.to_datetime(df_val[PAR['forecast_horizon']:].index).to_series(),
                              baseline_method=baseline_method[i], forecast_horizon= PAR['forecast_horizon'])
            else:
                eval_baseline(mean_forecast, y_val[:, :, target_column],
                              upper_PI[i], lower_PI[i],
                              PAR['cap_limit'],
                              hours=pd.to_datetime(df_val[PAR['forecast_horizon']:].index).to_series(),
                              baseline_method=baseline_method[i], forecast_horizon=PAR['forecast_horizon'])


    except KeyboardInterrupt:
        print()
        print('manual interrupt')
    finally:
        print(','.join(CALC_BASELINES) +'fitted and evaluated')

if __name__ == '__main__':
    ARGS, LOSS_OPTIONS = parse_with_loss()
    PAR = read_config(model_name=ARGS.station, config_path=ARGS.config, main_path=MAIN_PATH)
    OUTDIR = os.path.join(MAIN_PATH, PAR['evaluation_path'])
    SCALE_DATA = True
    LIMIT_HISTORY = True
    NUM_PRED = False
    SLIDING_WINDOW = 1
    CALC_BASELINES =['sarimax','simple-naive','seasonal-naive','naive-stl','ets','garch']
    DAY_FIRST = True
    ORDER = (3, 1, 0)
    sORDER = (2, 0, 0, 24)
    SEASONALITY=24
    ALPHA = 1.96
    EXOG = True
    APPLY_EXISTING_MODEL = True
    main(infile=os.path.join(MAIN_PATH, PAR['data_path']), target_id=PAR['target_id'])