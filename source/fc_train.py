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
"""
Train an RNN model for load forecasting based on provided data.

Train an RNN model on prepared data loaded as pandas dataframe from a csv file. 
Hyperparameter exploration using optuna is also possible if desired. 
The trained model is saved at the location specified under "output_path": in the corresponding 
config.json and can be loaded via torch.load() or evaluated by using the fc_evaluate.py script. 
This script scales the data, loads a custom datastructure and then generates and trains a neural net.

Notes
-----
Hyperparameter exploration

- Any training parameter is considered a hyperparameter as long as it is specified in
    either config.json or tuning.json. The latter is the standard file where the (so far) 
    best found configuration is saved and should usually not be manually adapted unless 
    new tests are for some reason not comparable to older ones (e.g. after changing the loss function).
- Possible hyperparameters are: target_column, encoder_features, decoder_features,
    max_epochs, learning_rate, batch_size, shuffle, history_horizon, forecast_horizon, 
    train_split, validation_split, core_net, relu_leak, dropout_fc, dropout_core, 
    rel_linear_hidden_size, rel_core_hidden_size, optimizer_name, cuda_id
"""

from torch.utils.tensorboard import SummaryWriter
#TODO: tensorboard necessitates chardet, which is licensed under LGPL: https://pypi.org/project/chardet/
#if 'exploration' is used, this would violate our license policy as LGPL is not compatible with apache
from utils.config_util import read_config, write_config, parse_with_loss, query_true_false, clean_up_tensorboard_dir
from utils.csv_logger import create_log, log_data, write_log_to_csv

# import json
import os
import sys
import warnings
import argparse

from datetime import datetime
from time import perf_counter  # ,localtime

# from itertools import product
# from tensorboard.plugins.hparams import api as hp

import pandas as pd
import torch
import numpy as np

# import plotly
import optuna


MAIN_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
print(MAIN_PATH)
sys.path.append(MAIN_PATH)

# Do relative imports below this

from utils.config_util import (
    read_config,
    write_config,
    parse_with_loss,
    query_true_false,
    clean_up_tensorboard_dir,
)
from utils.csv_logger import create_log, log_data, write_log_to_csv
import utils.datatuner as dt
import utils.tensorloader as dl
import utils.fc_network as fc_net
import utils.modelhandler as mh

# import utils.parameterhandler as ph
# import utils.eval_metrics as metrics

# ToDo:
#      1. Decide whether exploration is to be overwritten.
#      2. Also, look for visualization possibilities.


# import shutil

# import tempfile

torch.set_printoptions(linewidth=120)  # Display option for output
torch.set_grad_enabled(True)


torch.manual_seed(1)


class flag_and_store(argparse._StoreAction):

    def __init__(self, option_strings, dest, dest_const, const, nargs=0, **kwargs):
        self.dest_const = dest_const
        if isinstance(const, list):
            self.val = const[1:]
            self.flag = const[0]
        else:
            self.val = []
            self.flag = const
        if isinstance(nargs, int):
            nargs_store = nargs + len(self.val)
        else:
            nargs_store = nargs
        super(flag_and_store, self).__init__(
            option_strings, dest, const=None, nargs=nargs_store, **kwargs
        )
        self.nargs = nargs

    def __call__(self, parser, namespace, values, option_strings=None):
        setattr(namespace, self.dest_const, self.flag)
        if isinstance(values, list):
            self.val.extend(values)
        elif values is not None:
            self.val.append(values)

        if len(self.val) == 1:
            self.val = self.val[0]
        super().__call__(parser, namespace, self.val, option_strings)


warnings.filterwarnings("ignore")


def set_optimizer(name, model, learning_rate):
    """
    Specify which optimizer to use during training.

    Initialize a torch.optim optimizer for the given model based on the specified name and learning rate.

    Parameters
    ----------
    name : string or None, default = 'adam'
        The name of the torch.optim optimizer to be used. The following
        strings are accepted as arguments: 'adagrad', 'adam', 'adamax', 'adamw', 'rmsprop', or 'sgd'
    model : utils.fc_network.EncoderDecoder
        The model which is to be optimized
    learning_rate : float or None
        The learning rate to be used by the optimizer. If set to None, the default value as defined in
        torch.optim is used

    Returns
    -------
    torch.optim optimizer class
        A torch.optim optimizer that implements one of the following algorithms:
        Adagrad, Adam, Adamax, AdamW, RMSprop, or SGD (stochastic gradient descent)
        SGD is set to use a momentum of 0.5.

    """


    if name == "adam":
        optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    if name == "sgd":
        optimizer = torch.optim.SGD(model.parameters(), lr=learning_rate, momentum=0.5)
    if name == "adamw":
        optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    if name == "adagrad":
        optimizer = torch.optim.Adagrad(model.parameters(), lr=learning_rate)
    if name == "adamax":
        optimizer = torch.optim.Adamax(model.parameters(), lr=learning_rate)
    if name == "rmsprop":
        optimizer = torch.optim.RMSprop(model.parameters(), lr=learning_rate)
    return optimizer


def make_model(
    df: pd.DataFrame,
    scalers,
    encoder_features,
    decoder_features,
    batch_size,
    history_horizon,
    forecast_horizon,
    train_split,
    validation_split,
    core_net,
    relu_leak,
    dropout_fc,
    dropout_core,
    rel_linear_hidden_size,
    rel_core_hidden_size,
    core_layers,
    target_id,
    cuda_id,
    **_,
):
    """
    Provide a model that has been prepared for training.

    Split the given data frame into training, validation and testing sets and specify 
    encoder and decoder features. Use specified device for CUDA tensors if available.

    Parameters
    ----------
    df : pandas.DataFrame
        The data frame containing the model features, to be split into sets for training
    scalers : dict
        A dict of sklearn.preprocessing scalers with their corresponding feature group
        names (e.g."main", "add") as keywords
    encoder_features : string list
        A list containing desired encoder feature names as strings 
    decoder_features : string list
        A list containing desired decoder feature names as strings
    batch_size : int scalar
        The size of a batch for the tensor data loader
    history_horizon : int scalar
        The length of the history horizon in hours
    forecast_horizon : int scalar
        The length of the forecast horizon in hours
    train_split : float scalar
        Where to split the data frame for the training set, given as a fraction of data frame length
    validation_split : float scalar
        Where to split the data frame for the validation set, given as a fraction of data frame length
    core_net : string
        The name of the core_net, for example 'torch.nn.GRU'
    relu_leak : float scalar
        The value of the negative slope for the LeakyReLU
    dropout_fc : float scalar
        The dropout probability for the decoder
    dropout_core : float scalar
        The dropout probability for the core_net
    rel_linear_hidden_size : float scalar
        The relative linear hidden size, as a fraction of the total number of features in the training data
    rel_core_hidden_size : float scalar
        The relative core hidden size, as a fraction of the total number of features in the training data
    core_layers : int scalar
        The number of layers of the core_net
    target_id : string
        The name of feature to forecast, e.g. "demand"
    cuda_id : Union[torch.device, str, int]
        The device index to use for CUDA tensors

    Returns
    -------
    utils.fc_network.EncoderDecoder
        The prepared model with desired encoder/decoder features
    utils.tensorloader.CustomTensorDataLoader
        The training data loader
    utils.tensorloader.CustomTensorDataLoader
        The validation data loader 
    utils.tensorloader.CustomTensorDataLoader
        The test data loader 
    """

    if torch.cuda.is_available():
        DEVICE = "cuda"
        if cuda_id is not None:
            torch.cuda.set_device(cuda_id)
    else:
        DEVICE = "cpu"

    split_index = int(len(df.index) * train_split)
    subsplit_index = int(len(df.index) * validation_split)

    df_train = df.iloc[0:split_index]
    df_val = df.iloc[split_index:subsplit_index]
    df_test = df.iloc[subsplit_index:]

    print("Size training set: \t{}".format(df_train.shape[0]))
    print("Size validation set: \t{}".format(df_val.shape[0]))

    # shape input data that is measured in the Past and can be fetched from UDW/LDW
    train_data_loader = dl.make_dataloader(
        df_train,
        target_id,
        encoder_features,
        decoder_features,
        history_horizon=history_horizon,
        forecast_horizon=forecast_horizon,
        batch_size=batch_size,
    ).to(DEVICE)
    validation_data_loader = dl.make_dataloader(
        df_val,
        target_id,
        encoder_features,
        decoder_features,
        history_horizon=history_horizon,
        forecast_horizon=forecast_horizon,
        batch_size=batch_size,
    ).to(DEVICE)
    test_data_loader = dl.make_dataloader(
        df_test,
        target_id,
        encoder_features,
        decoder_features,
        history_horizon=history_horizon,
        forecast_horizon=forecast_horizon,
        batch_size=1,
    ).to(DEVICE)

    in_size1 = train_data_loader.number_features1()
    in_size2 = train_data_loader.number_features2()
    net = fc_net.EncoderDecoder(
        input_size1=in_size1,
        input_size2=in_size2,
        out_size=ARGS.num_pred,
        dropout_fc=dropout_fc,
        dropout_core=dropout_core,
        rel_linear_hidden_size=rel_linear_hidden_size,
        rel_core_hidden_size=rel_core_hidden_size,
        scalers=scalers,
        core_net=core_net,
        relu_leak=relu_leak,
        core_layers=core_layers,
        criterion=ARGS.loss,
    )

    return net, train_data_loader, validation_data_loader, test_data_loader


def train(
    train_data_loader,
    validation_data_loader,
    test_data_loader,
    net,
    learning_rate=None,
    batch_size=None,
    history_horizon=None,
    forecast_horizon=None,
    core_net=None,
    relu_leak=None,
    dropout_fc=None,
    dropout_core=None,
    rel_linear_hidden_size=None,
    rel_core_hidden_size=None,
    cuda_id=None,
    log_df=None,
    optimizer_name=None,
    max_epochs=None,
    logging_tb=True,
    **_,
):
    """
    Train the given model.

    Train the provided model using the given parameters for up to the specified number of epochs, with early stopping.
    Log the training data (optionally using TensorBoard's SummaryWriter)
    Finally, determine the score of the resulting best net.

    Parameters
    ----------
    train_data_loader : utils.tensorloader.CustomTensorDataLoader
        The training data loader
    validation_data_loader : utils.tensorloader.CustomTensorDataLoader
        The validation data loader
    test_data_loader : utils.tensorloader.CustomTensorDataLoader
        The test data loader    
    net : utils.fc_network.EncoderDecoder
        The model to be trained
    learning_rate : float, optional
        The specified optimizer's learning rate
    batch_size :  int scalar, optional   
        The size of a batch for the tensor data loader
    history_horizon : int scalar, optional
        The length of the history horizon in hours
    forecast_horizon : int scalar, optional
        The length of the forecast horizon in hours
    core_net : string, optional
        The name of the core_net, for example 'torch.nn.GRU'
    relu_leak : float scalar, optional
        The value of the negative slope for the LeakyReLU
    dropout_fc : float scalar, optional
        The dropout probability for the decoder
    dropout_core : float scalar, optional
        The dropout probability for the core_net
    rel_linear_hidden_size : float scalar, optional
        The relative linear hidden size, as a fraction of the total number of features in the training data
    rel_core_hidden_size : float scalar, optional
        The relative core hidden size, as a fraction of the total number of features in the training data
    cuda_id : Union[torch.device, str, int], optional
        The device index to use for CUDA tensors
    log_df : pandas.DataFrame, optional
        A DataFrame in which the results and parameters of the training are logged
    optimizer_name : string, optional
        The name of the torch.optim optimizer to be used. Currently only the following 
        strings are accepted as arguments: 'adagrad', 'adam', 'adamax', 'adamw', 'rmsprop', or 'sgd'
    max_epochs : int scalar, optional
        The maximum number of training epochs
    logging_tb : bool, default = True
        Specifies whether TensorBoard's SummaryWriter class should be used for logging during the training
    
    Returns
    -------
    utils.fc_network.EncoderDecoder
        The trained model
    pandas.DataFrame
        A DataFrame in which the results and parameters of the training have been logged
    float
        The minimum validation loss of the trained model
    float or torch.Tensor
        The score returned by the performance test. The data type depends on which metric was used. 
        The current implementation calculates the Mean Interval Score and returns either a float, or 1d-Array with the MIS along the horizon.
        A lower score is generally better
    """
    if torch.cuda.is_available():
        DEVICE = "cuda"
        if cuda_id is not None:
            torch.cuda.set_device(cuda_id)
    else:
        DEVICE = "cpu"

    net.to(DEVICE)
    criterion = ARGS.loss
    # to track the validation loss as the model trains
    valid_losses = []

    early_stopping = mh.EarlyStopping()
    optimizer = set_optimizer(optimizer_name, net, learning_rate)

    inputs1, inputs2, targets = next(iter(train_data_loader))

    # always have logging disabled for ci to avoid failed jobs because of tensorboard
    # if ARGS.ci:
    #    logging_tb = False

    if logging_tb:

        # if no run_name is given in command line with --logname, use timestamp
        if not ARGS.logname:
            ARGS.logname = str(datetime.now()).replace(":", "-").split(".")[0] + "/"

        # if exploration is True, don't save each trial in the same folder, that confuses tensorboard.
        # Instead, make a subfolder for each trial and name it Trial_{ID}.
        # If Trial ID > n_trials, actual training has begun; name that folder differently
        if PAR["exploration"]:
            ARGS.logname = ARGS.logname.split("/")[0] + "/trial_{}".format(
                PAR["trial_id"]
            )

        run_dir = os.path.join(MAIN_PATH, str("runs/" + ARGS.logname))
        tb = SummaryWriter(log_dir=run_dir)

        print("Begin training,\t tensorboard log here:\t", tb.log_dir)
        tb.add_graph(net, [inputs1, inputs2])
    else:
        print("Begin training...")

    t0_start = perf_counter()
    step_counter = 0
    final_epoch_loss = 0.0

    for name, param in net.named_parameters():
        torch.nn.init.uniform_(param.data, -0.08, 0.08)

    for epoch in range(max_epochs):
        epoch_loss = 0.0
        t1_start = perf_counter()
        net.train()
        for (inputs1, inputs2, targets) in train_data_loader:
            prediction, _ = net(inputs1, inputs2)
            optimizer.zero_grad()
            loss = criterion(targets, prediction, **(LOSS_OPTIONS))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(), 1)
            optimizer.step()
            step_counter += 1
            epoch_loss += loss.item() / len(train_data_loader)
            final_epoch_loss = epoch_loss
        t1_stop = perf_counter()

        with torch.no_grad():
            net.eval()
            validation_loss = 0.0
            for (inputs1, inputs2, targets) in validation_data_loader:
                output, _ = net(inputs1, inputs2)
                validation_loss += criterion(
                    targets, output, **(LOSS_OPTIONS)
                ).item() / len(validation_data_loader)
                valid_losses.append(validation_loss)

        print(
            "Epoch {}/{}\t train_loss {:.2e}\t val_loss {:.2e}\t elapsed_time {:.2e}".format(
                epoch + 1, max_epochs, epoch_loss, validation_loss, t1_stop - t1_start
            )
        )

        if logging_tb:
            tb.add_scalar("train_loss", epoch_loss, epoch + 1)
            tb.add_scalar("val_loss", validation_loss, epoch + 1)
            tb.add_scalar("train_time", t1_stop - t1_start, epoch + 1)
            tb.add_scalar("total_time", t1_stop - t0_start, epoch + 1)
            tb.add_scalar("val_loss_steps", validation_loss, step_counter)

            for name, weight in net.decoder.named_parameters():
                tb.add_histogram(name, weight, epoch + 1)
                tb.add_histogram(f"{name}.grad", weight.grad, epoch + 1)
                #.add_scalar(f'{name}.grad', weight.grad, epoch + 1)

        early_stopping(validation_loss, net)
        if early_stopping.early_stop:
            print("Stop in earlier epoch. Loading best model state_dict.")
            # load the last checkpoint with the best model
            net.load_state_dict(torch.load(early_stopping.temp_dir + "checkpoint.pt"))
            break
    # Now we are working with the best net and want to save the best score with respect to new test data
    with torch.no_grad():
        net.eval()

        score = mh.performance_test(
            net,
            data_loader=test_data_loader,
            score_type="mis",
            horizon=forecast_horizon,
        ).item()

        if PAR["best_score"]:
            rel_score = mh.calculate_relative_metric(score, PAR["best_score"])
        else:
            rel_score = 0

    # ToDo: define logset, move to main. Log on line per script execution, fetch best trial if hp_true, python database or table (MongoDB)
    # Only log once per run so immediately if exploration is false and only on the trial_main_run if it's true
    if (not PAR["exploration"]) or (
        PAR["exploration"] and not isinstance(PAR["trial_id"], int)
    ):
        log_df = log_data(
            PAR,
            ARGS,
            LOSS_OPTIONS,
            t1_stop - t0_start,
            final_epoch_loss,
            early_stopping.val_loss_min,
            score,
            log_df,
        )

    if logging_tb:
        # list of hyper parameters for tensorboard, will be available for sorting in tensorboard/hparams
        # if hyperparam tuning is True, use hparams as defined in tuning.json
        if PAR["exploration"]:
            params = PAR["hyper_params"]
        # else use predefined hparams
        else:
            params = {}

        params.update(
            {
                "max_epochs": max_epochs,
                "learning_rate": learning_rate,
                "batch_size": batch_size,
                "optimizer_name": optimizer_name,
                "dropout_fc": dropout_fc,
            }
        )

        # metrics to evaluate the model by
        values = {
            "hparam/hp_total_time": t1_stop - t0_start,
            "hparam/score": score,
            "hparam/relative_score": rel_score,
        }
        # TODO: we could add the fc_evaluate images and metrics to tb to inspect the best model here.
        #tb.add_figure(f'{hour}th_hour-forecast')
        # update this to use run_name as soon as the feature is available in pytorch (currently nightly at 02.09.2020)
        # https://pytorch.org/docs/master/tensorboard.html
        tb.add_hparams(params, values)
        tb.close()

        clean_up_tensorboard_dir(run_dir)

    return net, log_df, early_stopping.val_loss_min, score


def objective(selected_features, scalers, hyper_param, log_df, **_):
    """
    Implement an objective function for optimization with Optuna.

    Provide a callable for Optuna to use for optimization. The callable creates and trains a 
    model with the specified features, scalers and hyperparameters. Each hyperparameter triggers a trial.

    Parameters
    ----------
    selected_features : pandas.DataFrame 
        The data frame containing the model features, to be split into sets for training
    scalers : dict
        A dict of sklearn.preprocessing scalers with their corresponding feature group
        names (e.g."main", "add") as keywords
    hyper_param: dict
        A dictionary containing hyperparameters for the Optuna optimizer
    log_df : pandas.DataFrame
        A DataFrame in which the results and parameters of the training are logged

    Returns
    -------
    Callable
        A callable that implements the objective function. Takes an optuna.trial._trial.Trial as an argument, and is used as the first argument of a call to optuna.study.Study.optimize()   
    
    Raises
    ------
    optuna.exceptions.TrialPruned
        If the trial was pruned
    """

    def search_params(trial):
        if PAR["exploration"]:
            param = {}
            # for more hyperparam, add settings and kwargs in a way compatible with
            # trial object(and suggest methods) of optuna
            for key in hyper_param["settings"].keys():
                print("Creating parameter: ", key)
                func_generator = getattr(
                    trial, hyper_param["settings"][key]["function"]
                )
                param[key] = func_generator(**(hyper_param["settings"][key]["kwargs"]))

        PAR.update(param)
        PAR["hyper_params"] = param
        PAR["trial_id"] = PAR["trial_id"] + 1

        model, train_dl, validation_dl, test_dl = make_model(
            selected_features, scalers, **PAR
        )
        _, _, val_loss, _ = train(
            train_data_loader=train_dl,
            validation_data_loader=validation_dl,
            test_data_loader=test_dl,
            log_df=log_df,
            net=model,
            **PAR,
        )
        # Handle pruning based on the intermediate value.
        if trial.should_prune():
            raise optuna.exceptions.TrialPruned()

        return val_loss

    return search_params


def main(infile, outmodel, target_id, log_path=None):
    # Read load data
    df = pd.read_csv(infile, sep=";", index_col=0)

    dt.fill_if_missing(df)

    if "target_list" in PAR:
        if PAR["target_list"] is not None:
            df[target_id] = df[PAR["target_list"]].sum(axis=1)

    selected_features, scalers = dt.scale_all(df, **PAR)

    path = os.path.join(
        MAIN_PATH,
        PAR["log_path"],
        PAR["model_name"],
        PAR["model_name"] + "_training.csv",
    )
    # print(path)
    log_df = create_log(
        os.path.join(
            MAIN_PATH,
            PAR["log_path"],
            PAR["model_name"],
            PAR["model_name"] + "_training.csv",
        ),
        os.path.join(MAIN_PATH, "targets", ARGS.station),
    )

    min_net = None

    if "best_loss" in PAR and PAR["best_loss"] is not None:
        min_val_loss = PAR["best_loss"]
    else:
        min_val_loss = np.inf
    try:
        if PAR["exploration"]:
            timeout = None
            hyper_param = read_config(
                model_name=ARGS.station,
                config_path=PAR["exploration_path"],
                main_path=MAIN_PATH,
            )
            if "number_of_tests" in hyper_param.keys():
                n_trials = hyper_param["number_of_tests"]
                PAR["n_trials"] = n_trials
            if "timeout" in hyper_param.keys():
                timeout = hyper_param["timeout"]

            # Set up the median stopping rule as the pruning condition.
            sampler = optuna.samplers.TPESampler(
                seed=10
            )  # Make the sampler behave in a deterministic way.
            study = optuna.create_study(
                sampler=sampler,
                direction="minimize",
                pruner=optuna.pruners.MedianPruner(),
            )
            print(
                "Max. number of iteration trials for hyperparameter tuning: ", n_trials
            )
            if 'timeout' in hyper_param.keys():
                if "parallel_jobs" in hyper_param.keys():
                    parallel_jobs = hyper_param["parallel_jobs"]
                    study.optimize(
                        objective(
                            selected_features, scalers, hyper_param, log_df=log_df, **PAR
                        ),
                        n_trials=n_trials,
                        n_jobs=parallel_jobs,
                        timeout=timeout,
                    )
                else:
                    study.optimize(
                        objective(
                            selected_features, scalers, hyper_param, log_df=log_df, **PAR
                        ),
                        n_trials=n_trials,
                        timeout=timeout,
                    )  
            else:
                if "parallel_jobs" in hyper_param.keys():
                    parallel_jobs = hyper_param["parallel_jobs"]
                    study.optimize(
                        objective(
                            selected_features, scalers, hyper_param, log_df=log_df, **PAR
                        ),
                        n_trials=n_trials,
                        n_jobs=parallel_jobs,
                    )
                else:
                    study.optimize(
                        objective(
                            selected_features, scalers, hyper_param, log_df=log_df, **PAR
                        ),
                        n_trials=n_trials,
                    )

            print("Number of finished trials: ", len(study.trials))
            # trials_df = study.trials_dataframe()

            if not os.path.exists(os.path.join(MAIN_PATH, PAR["log_path"])):
                os.mkdir(os.path.join(MAIN_PATH, PAR["log_path"]))

            print("Best trial:")
            trial = study.best_trial
            print("  Value: ", trial.value)
            print("  Params: ")
            for key, value in trial.params.items():
                print("    {}: {}".format(key, value))

            if min_val_loss > study.best_value:
                if not ARGS.ci:
                    if query_true_false("Overwrite config with new parameters?"):
                        PAR["best_loss"] = study.best_value
                        PAR.update(trial.params)
                	# TODO: check if one can retrieve a checkpoint/model from best trial in optuna, to omit redundant training
                    else:
                        print("Training with hyper parameters fetched from config.json")
                else:
                    print("Training with hyper parameters fetched from config.json")

        PAR["trial_id"] = "main_run"
        model, train_dl, validation_dl, test_dl = make_model(
            selected_features, scalers, **PAR
        )
        net, log_df, loss, new_score = train(
            train_data_loader=train_dl,
            validation_data_loader=validation_dl,
            test_data_loader=test_dl,
            net=model,
            log_df=log_df,
            **PAR,
        )

        if "best_score" in PAR and PAR["best_score"] is not None:
            score_sofar = PAR["best_score"]
        else:
            score_sofar = np.inf

        if score_sofar > new_score:
            min_net = net
            PAR["best_score"] = new_score
            PAR["best_loss"] = loss

            if PAR["exploration"]:
                if not ARGS.ci:
                    if query_true_false("Overwrite config with new parameters?"):
                        print("study best value: ", study.best_value)
                        print("current loss: ", loss)
                        PAR["best_loss"] = study.best_value
                        PAR.update(trial.params)

            print(
                "Model improvement achieved. Save {}-file in {}.".format(
                    ARGS.station, PAR["output_path"]
                )
            )
            if PAR["exploration"]:
                if not ARGS.ci:
                    PAR["exploration"] = not query_true_false(
                        "Parameters tuned and updated. Do you wish to turn off hyperparameter tuning for future training?"
                    )
        else:
            print(
                "Existing model for this target did not improve in current run. Discard temporary model files."
            )

    except KeyboardInterrupt:
        print("manual interrupt")
    finally:
        if min_net is not None:
            print("saving model")
            if not os.path.exists(
                os.path.join(MAIN_PATH, PAR["output_path"])
            ):  # make output folder if it doesn't exist
                os.makedirs(os.path.join(MAIN_PATH, PAR["output_path"]))
            torch.save(min_net, outmodel)

            # drop unnecessary helper vars befor using PAR to save config
            PAR.pop("hyper_params", None)
            PAR.pop("trial_id", None)
            PAR.pop("n_trials", None)

            write_config(
                PAR,
                model_name=ARGS.station,
                config_path=ARGS.config,
                main_path=MAIN_PATH,
            )

        if log_df is not None:
            print("saving log")
            write_log_to_csv(
                log_df,
                os.path.join(MAIN_PATH, PAR["log_path"], PAR["model_name"]),
                PAR["model_name"] + "_training.csv",
            )


if __name__ == "__main__":
    ARGS, LOSS_OPTIONS = parse_with_loss()
    PAR = read_config(
        model_name=ARGS.station, config_path=ARGS.config, main_path=MAIN_PATH
    )
    if PAR["exploration"]:
        PAR["trial_id"] = 0  # set global trial ID for logging trials in subfolders
    main(
        infile=os.path.join(MAIN_PATH, PAR["data_path"]),
        outmodel=os.path.join(MAIN_PATH, PAR["output_path"], PAR["model_name"]),
        target_id=PAR["target_id"],
        log_path=os.path.join(MAIN_PATH, PAR["log_path"]),
    )
