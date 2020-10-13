import pandas as pd
import numpy as np
import sys
import json
import os

MAIN_PATH = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(MAIN_PATH)

from plf_util.config_util import read_config, parse_basic
#Import customized functions below this point

import plf_util.datatuner as dt

def load_raw_data_xlsx(files):
    # files is an array of maps
    # the maps contain the following data with the keyword (keyword)
    # ('file_name') the name of the xlsx file
    # ('date_column') the name of the date_column in the raw_data
    # ('time_zone') specifier for the timezone the raw data is recorded in
    # ('sheet_name') name or list of names of the sheets that are to be read
    # ('combine') boolean, all datasheets with true are combined into one, all others are read individually
    # ('start_column') Columns between this and ('end_column') are loaded
    # ('end_column')

    '''Read load data. The source load_file is assumed to be a xlsx load_file and to have an hourly resolution'''
    print('Importing XLSX Data...')

    combined_files = []
    individual_files = []

    for xlsx_file in files:
        print('importing ' + xlsx_file['file_name'])
    # if isinstance(file_name, str):
    #     file_name = [file_name,'UTC']
        date_column = xlsx_file['date_column']
        raw_data = pd.read_excel(INPATH + xlsx_file['file_name'], xlsx_file['sheet_name'],
                                parse_dates=[date_column])

        # convert load data to UTC
        if(xlsx_file['time_zone'] != 'UTC'):
            raw_data[date_column] = pd.to_datetime(raw_data[date_column]).dt.tz_localize(xlsx_file['time_zone'], ambiguous="infer").dt.tz_convert('UTC').dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            if (xlsx_file['dayfirst']):
                raw_data[date_column] = pd.to_datetime(raw_data[date_column], format='%d-%m-%Y %H:%M:%S').dt.tz_localize(None)
            else:
                raw_data[date_column] = pd.to_datetime(raw_data[date_column], format='%Y-%m-%d %H:%M:%S').dt.tz_localize(None)

        if(xlsx_file['data_abs']):
            raw_data.loc[:, xlsx_file['start_column']:xlsx_file['end_column']] = raw_data.loc[:, xlsx_file['start_column']:xlsx_file['end_column']].abs()
        # rename column IDs, specifically Time, this will be used later as the df index
        raw_data.rename(columns={date_column: 'Time'}, inplace=True)
        raw_data.head()  # now the data is positive and set to UTC
        raw_data.info()
        # interpolating for missing entries created by asfreq and original missing values if any
        raw_data.interpolate(method='time', inplace=True)

        if(xlsx_file['combine']):
            combined_files.append(raw_data)
        else:
            individual_files.append(raw_data)
    if(len(combined_files) > 0):
        individual_files.append(pd.concat(combined_files))
    return individual_files

def load_raw_data_csv(files):
    # files is an array of maps
    # the maps contain the following data with the keyword (keyword)
    # ('file_name') the name of the load_file
    # ('date_column') the name of the date_column in the raw_data
    # ('dayfirst') specifier for the formating of the read time
    # ('sep') separator used in this file
    # ('combine') boolean, all datasheets with true are combined into one, all others are read individually
    # ('use_columns') list of columns that are loaded

    '''Read i.e. temperature data. The source files are assumed to be csv, in hourly resolution. Windspeed and temperature'''
    print('Importing CSV Data...')


    combined_files = []
    individual_files = []

    for csv_file in files:
        print('Importing ' + csv_file['file_name'] + ' ...')
        date_column = csv_file['date_column']
        raw_data = pd.read_csv(INPATH + csv_file['file_name'], sep=csv_file['sep'], usecols=csv_file['use_columns'], parse_dates=[date_column] , dayfirst=csv_file['dayfirst'])
        # pd.read_csv(INPATH + name, sep=sep, usecols=cols, parse_dates=[date_column] , dayfirst=dayfirst)
        if (csv_file['time_zone'] != 'UTC'):
            raw_data[date_column] = pd.to_datetime(raw_data[date_column]).dt.tz_localize(csv_file['time_zone'], ambiguous="infer").dt.tz_convert('UTC').dt.strftime('%Y-%m-%d %H:%M:%S')
        else:
            if (csv_file['dayfirst']):
                raw_data[date_column] = pd.to_datetime(raw_data[date_column], format='%d-%m-%Y %H:%M:%S').dt.tz_localize(None)
            else:
                raw_data[date_column] = pd.to_datetime(raw_data[date_column], format='%Y-%m-%d %H:%M:%S').dt.tz_localize(None)

        print('...Importing finished. ')
        raw_data.rename(columns={date_column: 'Time'}, inplace=True)

        if(csv_file['combine']):
            combined_files.append(raw_data)
        else:
            individual_files.append(raw_data)

    if(len(combined_files) > 0):
        individual_files.append(pd.concat(combined_files, sort = False))
    #for frame in individual_files:
    #    frame.rename(columns={date_column: 'Time'}, inplace=True)
    return individual_files


def set_to_hours(df):
    '''setting the frequency as required'''
    df['Time'] = pd.to_datetime(df['Time'])
    df = df.set_index('Time')
    df = df.asfreq(freq='H')
    return df


if __name__ == '__main__':

    ARGS = parse_basic()
    config_file = os.path.join(MAIN_PATH, 'targets', ARGS.station, 'preprocessing.json')
    PAR = read_config(config_path=config_file)

    # DEFINES
    INPATH = os.path.join(MAIN_PATH, PAR['raw_path'])
    if ('xlsx_files' in PAR):
        XLSX_FILES = PAR['xlsx_files']
    if ('csv_files' in PAR):
        CSV_FILES = PAR['csv_files']
    OUTFILE = os.path.join(MAIN_PATH, PAR['data_path'])

    # Prepare Load Data
    df_list = []
    if ('xlsx_files' in PAR):
        xlsx_data = load_raw_data_xlsx(XLSX_FILES)
        for data in xlsx_data:
            hourly_data = set_to_hours(df=data)
            dt.fill_if_missing(hourly_data)
            df_list.append(hourly_data)

    if ('csv_files' in PAR):
        csv_data = load_raw_data_csv(CSV_FILES)
        for data in csv_data:
            hourly_data = set_to_hours(df=data)
            dt.fill_if_missing(hourly_data)
            print(hourly_data)
            df_list.append(hourly_data)

    print(df_list)
    # When concatenating the arrays are filled with NaNs if the index is not available.
    # since the dataframes were already interpolated there are non "natural" NaNs left so
    # droping all rows with NaNs finds the maximum overlapp in indices
    # # Merge load and weather data to one df
    df = pd.concat(df_list, axis = 1)

    df.dropna(inplace = True)

    if not df.index.equals(pd.date_range(min(df.index),max(df.index),freq = df.index.freq)):
        raise ValueError("DateTime index is not continuous")
    if not df.isnull().values.any():
        print('No missing data \n')
    df.head()

    ## http://blog.davidkaleko.com/feature-engineering-cyclical-features.html
    df['hour_sin'] = np.sin(df.index.hour * (2. * np.pi / 24))
    df['hour_cos'] = np.cos(df.index.hour * (2. * np.pi / 24))
    df['mnth_sin'] = np.sin((df.index.month - 1) * (2. * np.pi / 12))
    df['mnth_cos'] = np.cos((df.index.month - 1) * (2. * np.pi / 12))
    # fetch back the datetime again

    # add one-hot encoding for Hour & Month
    hours = pd.get_dummies(df.index.hour, prefix='hour').set_index(df.index)  # one-hot encoding of hours
    month = pd.get_dummies(df.index.month, prefix='month').set_index(df.index)  # one-hot encoding of month
    weekday = pd.get_dummies(df.index.dayofweek, prefix='weekday').set_index(df.index)  # one-hot encoding of month
    df = pd.concat([df, hours, month, weekday], axis=1)

    # store new df as csv
    df.head()
    df.to_csv(OUTFILE, sep=';', index=True)
