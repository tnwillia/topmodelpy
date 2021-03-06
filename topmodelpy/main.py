"""Main module that runs topmodelpy.

This module contains functionality that:
    - Read model configurationo file
    - Read all input files
    - Preprocess input data
        - Calculate the timestep daily fraction
        - Calculate pet if not in timeseries
        - Calculates adjusted precipitation from snowmelt
        - Calculate the twi weighted mean
    - Run Topmodel
    - Post process results
        - Write output *.csv file of results
        - Plot output
"""
import pandas as pd
from pathlib import PurePath
from topmodelpy import (hydrocalcs,
                        modelconfigfile,
                        parametersfile,
                        timeseriesfile,
                        twifile,
                        plots,
                        report)
from topmodelpy.topmodel import Topmodel


def topmodelpy(configfile, options):
    """Read inputs, preprocesse data, run Topmodel, and postprocess
    results, write *.csv outputfiles and make plots.

    :param configfile: The file path to the model config file that
    contains model specifications
    :type param: string
    :param options: The options sent from the cli
    :type options: Click.obj
    """
    config_data = modelconfigfile.read(configfile)
    parameters, timeseries, twi = read_input_files(config_data)

    preprocessed_data = preprocess(config_data, parameters, timeseries, twi)
    topmodel_data = run_topmodel(parameters, twi, preprocessed_data)
    postprocess(config_data, timeseries, preprocessed_data, topmodel_data)


def read_input_files(configdata):
    """Read input files from model configuration file.

    Returns a tuple of:
        dictionary from parameters file
        pandas.DataFrame from timeseries file
        pandas.DataFrame from twi file

    :param config: A ConfigParser object that behaves much like a dictionary.
    :type config: ConfigParser
    :return: Tuple of parameters dict, timeseries dataframe, twi dataframe
    :rtype: tuple
    """
    parameters = parametersfile.read(configdata["Inputs"]["parameters_file"])
    timeseries = timeseriesfile.read(configdata["Inputs"]["timeseries_file"])
    twi = twifile.read(configdata["Inputs"]["twi_file"])

    return parameters, timeseries, twi


def preprocess(config_data, parameters, timeseries, twi):
    """Preprocess data for topmodel run.

    Calculate timestep daily fraction, usually 1 for daily timesteps
        - 1 day = 86400 seconds
    Calculate pet if pet is not in timeseries dataframe
    Calculate snowmelt and adjusted precipitation from snowmelt routine
        - Snowmelt routine requires temperatures in Fahrenheit.
        - The temperature cutoff from the parameters dict is in Fahrenheit.
        - snowprecip is the adjusted precipitation from snowmelt.
        - The snowmelt and snowpack variables are not used at this time.
    Calculate the difference between the adjusted precip and pet for Topmodel.
    Calculate the weighted twi mean for Topmodel.

    :param parameters: The parameters for the model.
    :type parameters: Dict
    :param timeseries: A dataframe of all the timeseries data.
    :type timeseries: Pandas.DataFrame
    :param twi: A dataframe of all the twi data.
    :type twi: Pandas.DataFrame
    :return preprocessed_data: A dict of the calculated variables from
                               preprocessing.
    :rtype: dict
    """
    # Calculate the daily timestep as a fraction
    timestep_daily_fraction = (
        (timeseries.index[1] - timeseries.index[0]).total_seconds() / 86400.0
    )

    # Get pet as a numpy array from the input timeseries if it exists,
    # otherwise calculate it.
    if "pet" in timeseries.columns:
        pet = timeseries["pet"].to_numpy() * timestep_daily_fraction
    else:
        pet = hydrocalcs.pet(
            dates=timeseries.index.to_pydatetime(),
            temperatures=timeseries["temperature"].to_numpy(),
            latitude=parameters["latitude"]["value"],
            method="hamon"
        )
        pet = pet * timestep_daily_fraction

    # If snowmelt option is turned on, then compute snowmelt and the difference
    # between the adjusted precip with pet.
    # Otherwise, just compute the difference between the original precip with
    # pet.
    snowprecip, snowmelt, snowpack = None, None, None
    if config_data["Options"].getboolean("option_snowmelt"):
        # Calculate the adjusted precipitation based on snowmelt
        # Note: snowmelt function needs temperatures in Fahrenheit
        snowprecip, snowmelt, snowpack = hydrocalcs.snowmelt(
            timeseries["precipitation"].to_numpy(),
            timeseries["temperature"].to_numpy() * (9/5) + 32,
            parameters["snowmelt_temperature_cutoff"]["value"],
            parameters["snowmelt_rate_coeff_with_rain"]["value"],
            parameters["snowmelt_rate_coeff"]["value"],
            timestep_daily_fraction
        )

        # Calculate the difference between the adjusted precip (snowprecip)
        # and pet.
        precip_minus_pet = snowprecip - pet
    else:
        # Calculate the difference between the original precip and pet
        precip_minus_pet = timeseries["precipitation"].to_numpy() - pet

    # Calculate the twi weighted mean
    twi_weighted_mean = hydrocalcs.weighted_mean(values=twi["twi"],
                                                 weights=twi["proportion"])

    # Return a dict of calculated data
    preprocessed_data = {
        "timestep_daily_fraction": timestep_daily_fraction,
        "pet": pet,
        "precip_minus_pet": precip_minus_pet,
        "snowprecip": snowprecip,
        "snowmelt": snowmelt,
        "snowpack": snowpack,
        "twi_weighted_mean": twi_weighted_mean,
    }

    return preprocessed_data


def run_topmodel(parameters, twi, preprocessed_data):
    """Run Topmodel.

    :param parameters: The parameters for the model.
    :type parameters: Dict
    :param twi: A dataframe of all the twi data.
    :type twi: Pandas.DataFrame
    :param preprocessed_data: A dict of the calculated variables from
                              preprocessing.
    :type: dict
    :return topmodel_data: A dict of relevant data results from Topmodel
    :rtype: dict
    """
    # Initialize Topmodel
    topmodel = Topmodel(
        scaling_parameter=parameters["scaling_parameter"]["value"],
        saturated_hydraulic_conductivity=(
            parameters["saturated_hydraulic_conductivity"]["value"]
        ),
        macropore_fraction=parameters["macropore_fraction"]["value"],
        soil_depth_total=parameters["soil_depth_total"]["value"],
        soil_depth_ab_horizon=parameters["soil_depth_ab_horizon"]["value"],
        field_capacity_fraction=parameters["field_capacity_fraction"]["value"],
        latitude=parameters["latitude"]["value"],
        basin_area_total=parameters["basin_area_total"]["value"],
        impervious_area_fraction=parameters["impervious_area_fraction"]["value"],
        flow_initial=parameters["flow_initial"]["value"],
        twi_values=twi["twi"].to_numpy(),
        twi_saturated_areas=twi["proportion"].to_numpy(),
        twi_mean=preprocessed_data["twi_weighted_mean"],
        precip_available=preprocessed_data["precip_minus_pet"],
        timestep_daily_fraction=preprocessed_data["timestep_daily_fraction"]
    )

    # Run Topmodel
    topmodel.run()

    # Return a dict of relevant calculated values
    topmodel_data = {
        "flow_predicted": topmodel.flow_predicted,
        "saturation_deficit_avgs": topmodel.saturation_deficit_avgs,
        "saturation_deficit_locals": topmodel.saturation_deficit_locals,
        "unsaturated_zone_storages": topmodel.unsaturated_zone_storages,
        "root_zone_storages": topmodel.root_zone_storages,
    }

    return topmodel_data


def postprocess(config_data, timeseries, preprocessed_data, topmodel_data):
    """Postprocess data for output.

    Output csv files
    Plot timseries
    """
    # Get output timeseries data
    output_df = get_output_dataframe(timeseries,
                                     preprocessed_data,
                                     topmodel_data)

    # Get output comparison stats
    output_comparison_data = get_comparison_data(output_df)

    # Write output data
    write_output_csv(df=output_df,
                     filename=PurePath(
                         config_data["Outputs"]["output_dir"],
                         config_data["Outputs"]["output_filename"]))

    # Write output data matrices
    if config_data["Options"].getboolean("option_write_output_matrices"):
        write_output_matrices_csv(config_data, timeseries, topmodel_data)

    # Plot output data
    plot_output_data(df=output_df,
                     comparison_data=output_comparison_data,
                     path=config_data["Outputs"]["output_dir"])

    # Write report of output data
    write_output_report(df=output_df,
                        comparison_data=output_comparison_data,
                        filename=PurePath(
                            config_data["Outputs"]["output_dir"],
                            config_data["Outputs"]["output_report"]))


def get_output_dataframe(timeseries, preprocessed_data, topmodel_data):
    """Get the output data of interest.

    Returns a Pandas Dataframe of all output data of interest.
    """
    output_data = {}
    if preprocessed_data["snowprecip"] is not None:
        output_data["snowprecip"] = preprocessed_data["snowprecip"]

    if "pet" not in timeseries.columns:
        output_data["pet"] = preprocessed_data["pet"]

    output_data["precip_minus_pet"] = preprocessed_data["precip_minus_pet"]

    output_data["flow_predicted"] = topmodel_data["flow_predicted"]
    output_data["saturation_deficit_avgs"] = topmodel_data["saturation_deficit_avgs"]

    output_df = timeseries.assign(**output_data)

    return output_df


def get_comparison_data(output_df):
    """Get comparison statistics.

    Return a dictionary of descriptive statistics and if output data contains
    an observed flow, then compute the Nash-Sutcliffe statistic.
    """
    output_comparison_data = {}
    if "flow_observed" in output_df.columns:
        output_comparison_data["nash_sutcliffe"] = (
            hydrocalcs.nash_sutcliffe(
                observed=output_df["flow_observed"].to_numpy(),
                modeled=output_df["flow_predicted"].to_numpy())
        )
        output_comparison_data["absolute_error"] = (
            hydrocalcs.absolute_error(
                observed=output_df["flow_observed"].to_numpy(),
                modeled=output_df["flow_predicted"].to_numpy())
        )
        output_comparison_data["mean_squared_error"] = (
            hydrocalcs.mean_squared_error(
                observed=output_df["flow_observed"].to_numpy(),
                modeled=output_df["flow_predicted"].to_numpy())
        )

    return output_comparison_data


def write_output_csv(df, filename):
    """Write output timeseries to csv file.

    Creating a pandas Dataframe to ease of saving a csv.
    """
    df.rename = {
        "temperature": "temperature (celsius)",
        "precipitation": "precipitation (mm/day)",
        "pet": "pet (mm/day)",
        "precip_minus_pet": "precip_minus_pet (mm/day)",
        "flow_observed": "flow_observed (mm/day)",
        "flow_predicted": "flow_predicted (mm/day)",
        "saturation_deficit_avgs": "saturation_deficit_avgs (mm/day)",
        "snowprecip": "snowprecip (mm/day)",
    }
    df.to_csv(filename,
              float_format="%.2f")


def write_output_matrices_csv(config_data, timeseries, topmodel_data):
    """Write output matrices.

    Matrices are of size: len(timeseries) x len(twi_bins)

    The following are the matrices saved.
         saturation_deficit_locals
         unsaturated_zone_storages
         root_zone_storages
    """
    num_cols = topmodel_data["saturation_deficit_locals"].shape[1]
    header = ["bin_{}".format(i) for i in range(1, num_cols+1)]

    saturation_deficit_locals_df = (
        pd.DataFrame(topmodel_data["saturation_deficit_locals"],
                     index=timeseries.index)
    )

    unsaturated_zone_storages_df = (
        pd.DataFrame(topmodel_data["unsaturated_zone_storages"],
                     index=timeseries.index)
    )

    root_zone_storages_df = (
        pd.DataFrame(topmodel_data["root_zone_storages"],
                     index=timeseries.index)
    )

    saturation_deficit_locals_df.to_csv(
        PurePath(
            config_data["Outputs"]["output_dir"],
            config_data["Outputs"]["output_filename_saturation_deficit_locals"]
        ),
        float_format="%.2f",
        header=header,
    )

    unsaturated_zone_storages_df.to_csv(
        PurePath(
            config_data["Outputs"]["output_dir"],
            config_data["Outputs"]["output_filename_unsaturated_zone_storages"]
        ),
        float_format="%.2f",
        header=header,
    )

    root_zone_storages_df.to_csv(
        PurePath(
            config_data["Outputs"]["output_dir"],
            config_data["Outputs"]["output_filename_root_zone_storages"]
        ),
        float_format="%.2f",
        header=header,
    )


def plot_output_data(df, comparison_data, path):
    """Plot output timeseries."""
    for key, series in df.iteritems():
        filename = PurePath(path, "{}.png".format(key.split(" ")[0]))
        plots.plot_timeseries(
            dates=df.index.to_pydatetime(),
            values=series.values,
            mean=series.mean(),
            median=series.median(),
            mode=series.mode()[0],
            max=series.max(),
            min=series.min(),
            label="{} (mm/day)".format(key),
            filename=filename)

    plots.plot_flow_duration_curve(
        values=df["flow_predicted"].to_numpy(),
        label="flow_predicted (mm/day)",
        filename=PurePath(path, "flow_duration_curve.png"))

    if "flow_observed" in df.columns:
        plots.plot_timeseries_comparison(
            dates=df.index.to_pydatetime(),
            observed=df["flow_observed"].to_numpy(),
            modeled=df["flow_predicted"].to_numpy(),
            absolute_error=comparison_data["absolute_error"],
            nash_sutcliffe=comparison_data["nash_sutcliffe"],
            mean_squared_error=comparison_data["mean_squared_error"],
            label="flow (mm/day)",
            filename=PurePath(path, "flow_observed_vs_flow_predicted.png"))

        plots.plot_flow_duration_curve_comparison(
            observed=df["flow_observed"].to_numpy(),
            modeled=df["flow_predicted"].to_numpy(),
            label="flow (mm/day)",
            filename=PurePath(path, "flow_duration_curved_observed_vs_predicted.png"))


def write_output_report(df, comparison_data, filename):
    """Write an html web page with interactive plots."""
    plots_html_data = {}
    for key, value in df.iteritems():
        plots_html_data[key] = plots.plot_timeseries_html(
            dates=df.index.to_pydatetime(),
            values=value,
            label="{} (mm/day)".format(key))

    flow_duration_curve_data = {
        "flow_duration_curve_html": plots.plot_flow_duration_curve_html(
            values=df["flow_predicted"].to_numpy(),
            label="flow_predicted (mm/day)")
    }

    if comparison_data:
        comparison_plot_html = plots.plot_timeseries_comparison_html(
            dates=df.index.to_pydatetime(),
            observed=df["flow_observed"].to_numpy(),
            modeled=df["flow_predicted"].to_numpy(),
            absolute_error=comparison_data["absolute_error"],
            label="flow (mm/day)")
        comparison_data.update({"comparison_plot_html": comparison_plot_html})

        flow_duration_curve_comparison_hmtl = (
            plots.plot_flow_duration_curve_comparison_html(
                observed=df["flow_observed"].to_numpy(),
                modeled=df["flow_predicted"].to_numpy(),
                label="flow (mm/day)")
        )
        flow_duration_curve_data.update(
            {"flow_duration_curve_comparison_html": flow_duration_curve_comparison_hmtl}
        )

    report.save(df=df,
                plots=plots_html_data,
                comparison_data=comparison_data,
                flow_duration_curve_data=flow_duration_curve_data,
                filename=filename)
