import abc
import copy

import numpy as np
from numpy import log, log10, power

from scipy import stats

import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.iolib.table import SimpleTable
from statsmodels.iolib.summary import Summary
from statsmodels.iolib.tableformatting import fmt_params
from statsmodels.sandbox.regression.predstd import wls_prediction_std
from statsmodels.stats.outliers_influence import variance_inflation_factor

import data


class ModelException(Exception):
    """Exception base class for the model.py module."""
    pass


class InvalidModelVariableNameError(ModelException):
    """Raised when an invalid model variable name is encountered"""
    pass


class InvalidVariableTransformError(ModelException):
    """Raise when an invalid variable transform is encountered."""
    pass


class RatingModel(abc.ABC):
    """Base class for rating models."""

    _transform_variable_names = {None: 'x',
                                 'log': 'log(x)',
                                 'log10': 'log10(x)',
                                 'pow2': 'power(x, 2)'
                                 }
    _transform_functions = {None: lambda x: x,
                            'log': log,
                            'log10': log10,
                            'pow2': lambda x: power(x, 2)
                            }
    _inverse_transform_functions = {None: lambda x: x,
                                    'log': np.exp,
                                    'log10': lambda x: power(10, x),
                                    'pow2': lambda x: power(x, 1/2)
                                    }
    _float_string_format = '{:.5g}'

    def __init__(self, data_manager, response_variable=None):
        """Initialize a RatingModel object.

        :param data_manager: Data manager containing response and explanatory variables.
        :type data_manager: data.DataManager
        :param response_variable:
        """

        if not isinstance(data_manager, data.DataManager):
            raise TypeError("data_manager must be type data.DataManager")

        self._data_manager = data_manager

        variable_names = data_manager.get_variable_names()

        # set the response variable, make sure it's valid
        if response_variable is None:
            self._response_variable = variable_names[0]
        else:
            if response_variable in variable_names:
                self._response_variable = response_variable
            else:
                raise InvalidModelVariableNameError(
                    "{} is not a valid response variable name.".format(response_variable))

        # initialize the explanatory variables attribute
        self._explanatory_variables = tuple(variable_names[1:])

        # noinspection PyUnresolvedReferences
        self._excluded_observations = pd.tseries.index.DatetimeIndex([], name='DateTime')
        self._model_dataset = pd.DataFrame()
        self._model_data_origin = pd.DataFrame(columns=['variable', 'origin'])

        # initialize the variable transform dictionary
        self._variable_transform = {}
        for variable in variable_names:
            self._variable_transform[variable] = None

        # initialize the model attribute
        self._model = None

    @staticmethod
    def _calc_plotting_position(x, a=0.5):
        """

        :param data:
        :param a:
        :return:
        """

        x = np.asarray(x)

        Nx = x.shape[0]

        sorted_index = np.argsort(x)

        rank = np.zeros(Nx, int)
        rank[sorted_index] = np.arange(Nx) + 1

        pp = (rank - a) / (Nx + 1 - 2 * a)

        return pp

    @staticmethod
    def _calc_quantile(x, q):
        """

        :param x:
        :param q:
        :return:
        """

        pp = RatingModel._calc_plotting_position(x)

        sorted_index = np.argsort(x)

        xp = x[sorted_index]
        pp = pp[sorted_index]

        quantile = np.interp(q, pp, xp)

        return quantile

    @classmethod
    def _check_transform(cls, transform):
        """

        :param transform:
        :return:
        """
        if transform not in cls._transform_variable_names.keys():
            raise InvalidVariableTransformError("{} is an unrecognized transformation.".format(transform))

    def _check_variable_names(self, variable_names):
        """

        :param variable_names:
        :type variable_names: abc.Iterable
        :return:
        """
        for variable in variable_names:
            if variable not in self._data_manager.get_variable_names():
                raise InvalidModelVariableNameError("{} is not a valid variable name.".format(variable),
                                                    variable)

    @abc.abstractmethod
    def _create_model(self):
        pass

    def _create_model_dataset(self):
        """

        :return:
        """

        mdl_dataset = self._data_manager.get_variable(self._response_variable)

        for variable in self._explanatory_variables:
            mdl_dataset[variable] = self._data_manager.get_variable(variable)

        self._model_dataset = mdl_dataset

        origin_data = []
        for variable in (self._response_variable,) + self._explanatory_variables:
            for origin in self._data_manager.get_variable_origin(variable):
                origin_data.append([variable, origin])

        self._model_data_origin = pd.DataFrame(data=origin_data, columns=['variable', 'origin'])

    def _get_dataset_table(self):
        """Create a SimpleTable for the model dataset

        :return:
        """

        model_dataset = self.get_model_dataset()
        index_as_str = np.expand_dims(model_dataset.index.astype(str), 1)
        observation_data = np.column_stack((index_as_str, model_dataset.as_matrix()))
        observation_data_headers = ['DateTime']
        observation_data_headers.extend(model_dataset.keys())
        observation_table = SimpleTable(data=observation_data,
                                        headers=observation_data_headers)

        return observation_table

    @classmethod
    def _get_variable_transform(cls, variable, transform):
        """

        :param variable:
        :param transform:
        :return:
        """

        return cls._transform_variable_names[transform].replace('x', variable)

    def exclude_observation(self, observation_time):
        """Exclude observation from the model.

        :param observation_time:
        :type observation_time: pandas.tseries.index.DatetimeIndex
        :return:
        """

        self._excluded_observations = self._excluded_observations.append(observation_time)
        self._excluded_observations = self._excluded_observations.sort_values()
        self._excluded_observations = self._excluded_observations.drop_duplicates()

        self._create_model()

    def get_excluded_observations(self):
        """Returns a time series of observations that have been excluded from the model.

        :return:
        """

        return copy.deepcopy(self._excluded_observations)

    def get_model_dataset(self):
        """Return a DataFrame containing the observations used in the current model."""

        model_dataset = pd.DataFrame(self._model_dataset.copy(deep=True))

        if model_dataset.shape != (0, 0):
            model_dataset.ix[:, 'Missing'] = model_dataset.isnull().any(axis=1)
            model_dataset.ix[:, 'Excluded'] = model_dataset.index.isin(self._excluded_observations)

        return model_dataset

    @abc.abstractmethod
    def get_model_summary(self):
        pass

    def get_response_variable(self):
        """Return the name of the current response variable"""

        return self._response_variable

    def get_variable_names(self):
        """Return a tuple containing the variable names within the model."""

        return tuple(self._data_manager.get_variable_names())

    def include_all_observations(self):
        """Include all observations that have previously been excluded."""

        self.include_observation(self._excluded_observations)
        self._create_model()

    def include_observation(self, observation_time):
        """Include the observation given the time of the response variable observation."""

        restored_index = self._excluded_observations.isin(observation_time)

        self._excluded_observations = self._excluded_observations[~restored_index]

        self._create_model()

    def set_response_variable(self, response_variable):
        """Set the response variable of the model.

        :param response_variable:
        :type response_variable: string
        :return:
        """

        self._check_variable_names([response_variable])

        self._response_variable = response_variable

        self.update_model()

    def transform_response_variable(self, transform):
        """Transform the response variable.

        :param transform: String representation of variable transform
        :return:
        """

        self._check_transform(transform)
        self._variable_transform[self._response_variable] = transform

        self._create_model()

    @abc.abstractmethod
    def predict_response_variable(self, explanatory_variable):
        """Predict the value of the response variable given values for the explanatory variable."""
        pass

    def update_model(self):
        """Update the regression model.

        :return:
        """

        self._create_model_dataset()
        self._create_model()


class OLSModel(RatingModel, abc.ABC):
    """Ordinary least squares (OLS) regression based rating model abstract class."""

    def _calc_ppcc(self):
        """Calculate the probability plot correlation coefficient

        :return:
        """

        res = self._model.fit()
        normal_quantile = self._calc_res_normal_quantile()

        ppcc, _ = stats.pearsonr(normal_quantile, res.resid)

        return ppcc

    def _calc_res_normal_quantile(self):
        """Calculate the normal quantiles of the residuals.

        :return:
        """

        res = self._model.fit()
        plotting_position = self._calc_plotting_position(res.resid)
        loc, scale = stats.norm.fit(res.resid)
        dist = stats.norm(loc, scale)
        normal_quantile = dist.ppf(plotting_position)

        quantile_series = pd.Series(index=res.resid.index, data=normal_quantile, name='Normal quantile of residual')

        return quantile_series

    def _create_model(self):
        """Create the ordinary least squares linear regression model.

        :return:
        """

        model_formula = self.get_model_formula()

        removed_observation_index = self._model_dataset.index.isin(self._excluded_observations)

        model = smf.ols(model_formula,
                        data=self._model_dataset,
                        subset=~removed_observation_index,
                        missing='drop')

        self._model = model

    @abc.abstractmethod
    def _get_exogenous_matrix(self, exogenous_df):
        pass

    def _get_left_summary_table(self, res):
        """Get the left side of the model summary table.

        :return:
        """

        number_of_observations = ('Number of observations', [self._float_string_format.format(res.nobs)])
        error_degrees_of_freedom = ('Error degrees of freedom', [self._float_string_format.format(res.df_resid)])
        rmse = ('Root mean squared error', [self._float_string_format.format(np.sqrt(res.mse_resid))])
        ppcc = ('Residual PPCC', [self._float_string_format.format(self._calc_ppcc())])

        gleft = [number_of_observations, error_degrees_of_freedom, rmse, ppcc]

        response_variable_transform = self._variable_transform[self._response_variable]

        if response_variable_transform:

            if response_variable_transform is 'log10':

                bcf = ('Non-parametric smearing bias correction factor',
                       [self._float_string_format.format(np.power(10, res.resid).mean())])

                gleft.append(bcf)

            elif response_variable_transform is 'log':

                bcf = ('Non-parmetric smearic bias correction factor',
                       [self._float_string_format.format(np.exp(res.resid).mean())])

                gleft.append(bcf)

        return gleft

    def _get_model_equation(self):
        """Get a string representation of the model equation with estimated coefficients.

        :return:
        """

        res = self._model.fit()

        explanatory_variables = []
        for variable in self._model.exog_names:
            if variable is 'Intercept':
                explanatory_variables.append(self._float_string_format.format(res.params[variable]))
            else:
                explanatory_variables.append(self._float_string_format.format(res.params[variable]) + variable)

        response_variable = self._model.endog_names

        model_equation = response_variable + ' = ' + ' + '.join(explanatory_variables)

        return SimpleTable(data=[[model_equation]], headers=['Linear regression model:'])

    def _get_params_summary(self, alpha=0.1):
        """create a summary table for the parameters

        Parameters
        ----------
        alpha : float
            significance level for the confidence intervals

        Returns
        -------
        params_table : SimpleTable instance
        """

        # TODO: Acknowledge that this code was modified from the statsmodels package

        results = self._model.fit()

        def forg(x, prec=3):
            if prec == 3:
                # for 3 decimals
                if (abs(x) >= 1e4) or (abs(x) < 1e-4):
                    return '%9.3g' % x
                else:
                    return '%9.3f' % x
            elif prec == 4:
                if (abs(x) >= 1e4) or (abs(x) < 1e-4):
                    return '%10.4g' % x
                else:
                    return '%10.4f' % x
            else:
                raise NotImplementedError

        # Parameters part of the summary table
        conf_int = results.conf_int(alpha)

        # Dictionary to store the header names for the parameter part of the
        # summary table. look up by modeltype
        alp = str((1 - alpha) * 100) + '%'

        param_header = ['coef', 'std err', 't', 'P>|t|',
                        '[' + alp + ' Conf. Int.]']

        xname = self._model.exog_names

        params_stubs = xname

        exog_idx = range(len(xname))

        # center confidence intervals if they are unequal lengths
        confint = ["%s %s" % tuple(map(forg, conf_int.ix[i])) for i in exog_idx]
        len_ci = list(map(len, confint))
        max_ci = max(len_ci)
        min_ci = min(len_ci)

        if min_ci < max_ci:
            confint = [ci.center(max_ci) for ci in confint]

        # explicit f/g formatting, now uses forg, f or g depending on values
        params_data = zip([forg(results.params[i], prec=4) for i in exog_idx],
                          [forg(results.bse[i]) for i in exog_idx],
                          [forg(results.tvalues[i]) for i in exog_idx],
                          # ["%#6.3f" % (results.pvalues[i]) for i in exog_idx],
                          ["%#6.3g" % (results.pvalues[i]) for i in exog_idx],
                          confint
                          )
        params_data = list(params_data)
        parameter_table = SimpleTable(params_data,
                                      param_header,
                                      params_stubs,
                                      txt_fmt=fmt_params
                                      )

        if results.params.shape[0] > 2:
            vif_table = self._get_vif_table()
            parameter_table.extend_right(vif_table)

        return parameter_table

    def _get_right_summary_table(self, res):
        """Get the right side of the model summary table.

        :return:
        """

        rsquared = ('R-squared', [self._float_string_format.format(res.rsquared)])
        adjusted_rsquared = ('Adjusted R-squared', [self._float_string_format.format(res.rsquared_adj)])
        fvalue = ('F-statistic vs. constant model', [self._float_string_format.format(res.fvalue)])
        pvalue = ('p-value', [self._float_string_format.format(res.f_pvalue)])

        gright = [rsquared, adjusted_rsquared, fvalue, pvalue]

        response_variable_transform = self._variable_transform[self._response_variable]

        if response_variable_transform:

            if response_variable_transform is 'log10':

                RMSE_pct = ('RMSE(%)',
                            [self._float_string_format.format(100*np.sqrt(np.exp(np.log(10)**2 * res.mse_resid)-1))])

                gright.append(RMSE_pct)

            elif response_variable_transform is 'log':

                RMSE_pct = ('RMSE(%)',
                            [self._float_string_format.format(
                                100 * np.sqrt(np.exp(res.mse_resid) - 1))])

                gright.append(RMSE_pct)

        return gright

    def _get_variable_summary(self, model_variables, table_title=''):
        """Get a summary of a variable.

        :param model_variables:
        :param table_title:
        :return:
        """

        table_data = [[''], ['Minimum'], ['1st Quartile'], ['Median'], ['Mean'], ['3rd Quartile'], ['Maximum']]

        number_format_str = '{:.5g}'

        q = np.array([0, 0.25, 0.5, 0.75, 1])

        excluded_observations = self._model_dataset.index.isin(self._excluded_observations) | \
            np.any(self._model_dataset.isnull(), axis=1)

        for variable in model_variables:

            variable_series = self._model_dataset.ix[~excluded_observations, variable]

            quantiles = self._calc_quantile(variable_series, q)

            table_data[0].append(variable)
            table_data[1].append(number_format_str.format(quantiles[0]))
            table_data[2].append(number_format_str.format(quantiles[1]))
            table_data[3].append(number_format_str.format(quantiles[2]))
            table_data[4].append(number_format_str.format(variable_series.mean()))
            table_data[5].append(number_format_str.format(quantiles[3]))
            table_data[6].append(number_format_str.format(quantiles[4]))

            variable_transform = self._variable_transform[variable]

            if variable_transform:

                variable_transform_name = self._get_variable_transform(variable, variable_transform)

                transform_function = self._transform_functions[variable_transform]

                transformed_variable_series = transform_function(variable_series)

                transform_quantiles = self._calc_quantile(transformed_variable_series, q)

                table_data[0].append(variable_transform_name)
                table_data[1].append(number_format_str.format(transform_quantiles[0]))
                table_data[2].append(number_format_str.format(transform_quantiles[1]))
                table_data[3].append(number_format_str.format(transform_quantiles[2]))
                table_data[4].append(number_format_str.format(transformed_variable_series.mean()))
                table_data[5].append(number_format_str.format(transform_quantiles[3]))
                table_data[6].append(number_format_str.format(transform_quantiles[4]))

        table_header = [table_title]

        table_header.extend([''] * (len(table_data[0])-1))

        variable_summary = SimpleTable(data=table_data, headers=table_header)

        return variable_summary

    def _get_vif_table(self):
        """Get a table containing the variance inflation factor for each predictor variable.

        :return:
        """

        vif_data = [['']]

        exog = self._model.exog

        # for variable in self._explanatory_variables:
        for exog_idx in range(1, exog.shape[1]):

            vif = variance_inflation_factor(exog, exog_idx)

            vif_data.append([self._float_string_format.format(vif)])

        vif_table = SimpleTable(vif_data, headers=['VIF'])

        return vif_table

    def get_explanatory_variable_summary(self):
        """Get a table of summary statistics for the explanatory variables. The summary statistics include:
            Minimum
            First quartile
            Median (second quartile)
            Mean
            Third quartile
            Maximum

        :return:
        """

        table_title = 'Explanatory variable summary'

        return self._get_variable_summary(self._explanatory_variables, table_title)

    def get_model_dataset(self):
        """Returns a pandas DataFrame containing the following columns:

            Date and time of observation
            Observed response variable
            Observed explanatory variables
            Missing and excluded observation indicators
            Fitted transformed response variable
            Raw residual
            Estimated response variable, with Duan smearing estimate applied
            Normal quantile
            Standardized (internally studentized) residual
            Leverage
            Cook's distance
            DFFITS

        :return:
        """

        model_dataset = super().get_model_dataset()

        res = self._model.fit()

        model_data_index = res.resid.index

        response_variable = self.get_response_variable()

        # add fitted values
        response_variable_transform = self._variable_transform[response_variable]
        transformed_response_variable_name = self._get_variable_transform(response_variable,
                                                                          response_variable_transform)
        fitted_values = res.fittedvalues.rename('Fitted ' + transformed_response_variable_name)

        # add raw residuals
        raw_residuals = res.resid.rename('Raw Residual')

        # add estimated response
        explanatory_data = data.DataManager(self._model_dataset.ix[model_data_index, :], self._model_data_origin)
        predicted_response = self.predict_response_variable(explanatory_data=explanatory_data, bias_correction=True)
        estimated_response = predicted_response[response_variable]
        estimated_response = estimated_response.rename('Estimated ' + response_variable)

        # add quantile
        quantile_series = self._calc_res_normal_quantile()

        ols_influence = res.get_influence()

        # add standardized residuals (also known as internally studentized residuals)
        standardized_residuals = pd.Series(index=model_data_index,
                                           data=ols_influence.resid_studentized_internal,
                                           name='Standardized Residual')

        # add leverage
        leverage = pd.Series(index=model_data_index,
                             data=ols_influence.hat_matrix_diag,
                             name='Leverage')

        # add Cook's D
        cooks_distance = pd.Series(index=model_data_index,
                                   data=ols_influence.cooks_distance[0],
                                   name="Cook's Distance")

        # add DFFITS
        dffits = pd.Series(index=model_data_index,
                           data=ols_influence.dffits[0],
                           name="DFFITS")

        model_dataset = pd.concat([model_dataset,
                                   fitted_values,
                                   raw_residuals,
                                   estimated_response,
                                   quantile_series,
                                   standardized_residuals,
                                   leverage,
                                   cooks_distance,
                                   dffits], axis=1)

        return model_dataset

    @abc.abstractmethod
    def get_model_formula(self):
        pass

    def get_model_report(self):
        """Get a report for the model. The report contains
            a summary of the model,
            the parameter variance-covariance matrix,
            model variable summary statistics,
            the origin files for the data, and
            a summary of the model dataset.


        :return:
        """

        # get a table for the data origins
        data_origin = []
        for variable in (self._response_variable, ) + self._explanatory_variables:
            for origin in self._data_manager.get_variable_origin(variable):
                if origin not in data_origin:
                    data_origin.append([origin])
        origin_table = SimpleTable(data=data_origin, headers=['Data file location'])

        observation_table = self._get_dataset_table()

        response_variable_summary = self.get_response_variable_summary()
        explanatory_variable_summary = self.get_explanatory_variable_summary()

        # variance-covariance matrix
        res = self._model.fit()
        X = self._model.exog
        x_prime_x_inverse = np.linalg.inv(np.matmul(X.transpose(), X))
        var_cov_matrix = res.mse_resid * x_prime_x_inverse
        var_cov_table = SimpleTable(data=var_cov_matrix,
                                    headers=self._model.exog_names,
                                    stubs=self._model.exog_names,
                                    title='Variance-covariance matrix',
                                    data_fmts=['%.5g'])

        empty_table = SimpleTable(data=[''])

        # get the model summary
        model_report = self.get_model_summary()

        model_report.tables.extend([empty_table,
                                    var_cov_table,
                                    response_variable_summary,
                                    explanatory_variable_summary,
                                    origin_table,
                                    observation_table])

        return model_report

    def get_model_summary(self):
        """Get summary statistics for the model.

        :return:
        """

        summary = Summary()

        # add the model equation with estimated parameters
        model_equation = self._get_model_equation()
        summary.tables.append(model_equation)

        # add the parameter summary
        params_summary = self._get_params_summary()
        summary.tables.append(params_summary)

        res = self._model.fit()

        # add more summary statistics
        gleft = self._get_left_summary_table(res)
        gright = self._get_right_summary_table(res)
        summary.add_table_2cols(res, gleft=gleft, gright=gright)

        # add extreme influence and outlier table
        high_leverage = ('High leverage:', self._float_string_format.format(3*res.params.shape[0]/res.nobs))
        extreme_outlier = ('Extreme outlier (Standardized residual):', self._float_string_format.format(3))
        dfn = res.params.shape[0] + 1
        dfd = res.nobs + res.params.shape[0]
        high_influence_cooksd = ("High influence (Cook's D)",
                                 self._float_string_format.format(stats.f.ppf(0.9, dfn=dfn, dfd=dfd)))
        high_influence_dffits = ("High influence (DFFITS)",
                                 self._float_string_format.format(2*np.sqrt(res.params.shape[0]/res.nobs)))
        influence_and_outlier_table_data = [high_leverage,
                                            extreme_outlier,
                                            high_influence_cooksd,
                                            high_influence_dffits]
        influence_and_outlier_table = SimpleTable(data=influence_and_outlier_table_data)
        summary.tables.append(influence_and_outlier_table)

        return summary

    def get_response_variable_summary(self):
        """Get a table of summary statistics for the response variable. The summary statistics include:
            Minimum
            First quartile
            Median (second quartile)
            Mean
            Third quartile
            Maximum

        :return:
        """

        table_title = 'Response variable summary'

        return self._get_variable_summary((self._response_variable, ), table_title)

    def predict_response_variable(self, explanatory_data=None, bias_correction=False, prediction_interval=False):
        """Predict the response of the model.

        :param explanatory_data:
        :param bias_correction:
        :param prediction_interval:
        :return:
        """

        if self._model:

            # get the model results
            res = self._model.fit()

            # get the explanatory data DataFrame
            if explanatory_data:
                explanatory_df = explanatory_data.get_data()
            else:
                explanatory_df = self._data_manager.get_data()

            exog = self._get_exogenous_matrix(explanatory_df)

            # predicted response variable
            mean_response = self._model.predict(res.params, exog=exog)
            mean_response = np.expand_dims(mean_response, axis=1)

            if prediction_interval:

                # confidence level for two - sided hypothesis
                confidence_level = 0.1  # 90% prediction interval
                confidence_level_text = '{:.1f}'.format(100*(1-confidence_level))

                _, interval_l, interval_u = wls_prediction_std(res, exog=exog, alpha=confidence_level)

                interval_l = np.expand_dims(interval_l, axis=1)
                interval_u = np.expand_dims(interval_u, axis=1)

                response_data = np.dstack((interval_l, mean_response, interval_u))

                columns = [self._response_variable + '_L' + confidence_level_text,
                           self._response_variable,
                           self._response_variable + '_U' + confidence_level_text
                           ]

            else:

                response_data = np.expand_dims(mean_response, axis=2)

                columns = [self._response_variable]

            if bias_correction:

                residuals = res.resid.as_matrix()
                residuals = np.expand_dims(residuals, axis=0)
                residuals = np.expand_dims(residuals, axis=2)
                residuals = np.tile(residuals, (response_data.shape[0], 1, response_data.shape[2]))

                response_data = np.tile(response_data, (1, residuals.shape[1], 1))

                prediction_results = np.mean(response_data + residuals, axis=1)

            else:

                prediction_results = np.squeeze(response_data, axis=1)

            response_variable_transform = self._variable_transform[self._response_variable]

            predicted_data = self._inverse_transform_functions[response_variable_transform](prediction_results)

            predicted = pd.DataFrame(data=predicted_data, index=explanatory_df.index, columns=columns)
            predicted = predicted.join(explanatory_df[list(self._explanatory_variables)], how='outer')

        else:

            predicted = pd.DataFrame(columns=[self._response_variable] + list(self._explanatory_variables))

        return predicted


class SimpleLinearRatingModel(OLSModel):
    """Class for OLS simple linear regression (SLR) ratings."""

    def __init__(self, data_manager, response_variable=None, explanatory_variable=None):
        """

        :param data_manager:
        :param response_variable:
        :param explanatory_variable:
        """

        super().__init__(data_manager, response_variable)

        if explanatory_variable:
            self.set_explanatory_variable(explanatory_variable)
        else:
            self.set_explanatory_variable(data_manager.get_variable_names()[1])

        # self.update_model()

    def _get_exogenous_matrix(self, exogenous_df):
        """

        :param exogenous_df:
        :return:
        """

        explanatory_variable = self.get_explanatory_variable()

        assert(explanatory_variable in exogenous_df.keys())

        exog = pd.DataFrame()

        explanatory_transform = self._variable_transform[explanatory_variable]
        transformed_variable_name = self._get_variable_transform(explanatory_variable, explanatory_transform)
        transform_function = self._transform_functions[explanatory_transform]
        exog[transformed_variable_name] = transform_function(exogenous_df[explanatory_variable])
        exog = sm.add_constant(exog)

        return exog

    def _get_left_summary_table(self, res):
        """

        :param res:
        :return:
        """

        gleft = super()._get_left_summary_table(res)

        removed_observation_index = self._model_dataset.index.isin(self._excluded_observations)
        null_value_index = self._model_dataset.isnull().any(axis=1)
        observation_index = ~(removed_observation_index | null_value_index)

        explanatory_variable_transform = self._variable_transform[self._explanatory_variables[0]]
        explanatory_transform_func = self._transform_functions[explanatory_variable_transform]
        x = explanatory_transform_func(self._model_dataset.ix[observation_index, self._explanatory_variables[0]])

        response_variable_transform = self._variable_transform[self._response_variable]
        response_transform_func = self._transform_functions[response_variable_transform]
        y = response_transform_func(self._model_dataset.ix[observation_index, self._response_variable])

        linear_corr = ('Linear correlation coefficient', [self._float_string_format.format(stats.pearsonr(x, y)[0])])

        gleft.append(linear_corr)

        return gleft

    def _get_right_summary_table(self, res):
        """

        :param res:
        :return:
        """

        gright = super()._get_right_summary_table(res)

        return gright

    def get_explanatory_variable(self):
        """Returns the name of the explanatory variable used in the SLR.

        :return: Name of explanatory variable
        """

        return self._explanatory_variables[0]

    def get_model_formula(self):
        """

        :return:
        """

        if self._response_variable and self._explanatory_variables[0]:

            explanatory_variable = self.get_explanatory_variable()

            response_var_transform = self._variable_transform[self._response_variable]
            model_response_var = self._get_variable_transform(self._response_variable, response_var_transform)

            explanatory_var_transform = self._variable_transform[explanatory_variable]
            model_explanatory_var = self._get_variable_transform(explanatory_variable, explanatory_var_transform)

            model_formula = model_response_var + ' ~ ' + model_explanatory_var

        else:

            model_formula = None

        return model_formula

    def set_explanatory_variable(self, variable):
        """

        :param variable:
        :return:
        """

        self._check_variable_names([variable])
        self._explanatory_variables = (variable,)
        self.update_model()

    def transform_explanatory_variable(self, transform):
        """

        :param transform:
        :return:
        """

        self._check_transform(transform)
        self._variable_transform[self._explanatory_variables[0]] = transform

        self._create_model()


class MultipleLinearRatingModel(OLSModel):
    """"""

    def __init__(self, data_manager, response_variable=None, explanatory_variables=None):
        """

        :param data_manager:
        :param response_variable:
        :param explanatory_variables:
        :return:
        """

        super().__init__(data_manager, response_variable)

        if explanatory_variables:
            self.set_explanatory_variables(explanatory_variables)
        else:
            self.set_explanatory_variables(data_manager.get_variable_names()[1:])

        # self.update_model()

    def _get_exogenous_matrix(self, exogenous_df):
        """

        :param exogenous_df:
        :return:
        """

        for variable in self._explanatory_variables:
            assert(variable in exogenous_df.keys())

        exog = pd.DataFrame()

        for variable in self._explanatory_variables:

            transform = self._variable_transform[variable]
            transform_function = self._transform_functions[transform]
            transformed_variable_name = self._get_variable_transform(variable, transform)
            exog[transformed_variable_name] = transform_function(exogenous_df[variable])

        exog = sm.add_constant(exog)

        return exog

    def get_explanatory_variables(self):
        """

        :return:
        """

        return tuple(self._explanatory_variables)

    def get_model_formula(self):
        """

        :return:
        """

        if self._response_variable and self._explanatory_variables[0]:

            response_var_transform = self._variable_transform[self._response_variable]
            model_response_var = self._get_variable_transform(self._response_variable, response_var_transform)

            explanatory_vars_transform = []
            for variable in self._explanatory_variables:
                explan_transform = self._variable_transform[variable]
                explanatory_vars_transform.append(self._get_variable_transform(variable, explan_transform))

            model_formula = model_response_var + ' ~ ' + ' + '.join(explanatory_vars_transform)

        else:

            model_formula = None

        return model_formula

    def set_explanatory_variables(self, variables):
        """

        :param variables:
        :return:
        """

        self._check_variable_names(variables)

        self._explanatory_variables = tuple(variables)

        self.update_model()

    def transform_explanatory_variable(self, explanatory_variable, transform):
        """

        :param explanatory_variable:
        :param transform:
        :return:
        """

        self._check_transform(transform)
        self._check_variable_names([explanatory_variable])
        self._variable_transform[explanatory_variable] = transform

        self._create_model()


class ComplexRatingModel(OLSModel):
    """"""

    def __init__(self, data_manager, response_variable=None, explanatory_variable=None):
        """

        :param data_manager:
        :param response_variable:
        :param explanatory_variable:
        """

        super().__init__(data_manager, response_variable)

        self._explanatory_variable_transform = [None]

        if explanatory_variable:
            self.set_explanatory_variable(explanatory_variable)
        else:
            self.set_explanatory_variable(data_manager.get_variable_names()[1])

        # self.update_model()

    def _get_exogenous_matrix(self, exogenous_df):
        """

        :return:
        """

        explanatory_variable = self.get_explanatory_variable()

        assert(explanatory_variable in exogenous_df.keys())

        exog = pd.DataFrame()

        for transform in self._explanatory_variable_transform:

            transformed_variable_name = self._get_variable_transform(explanatory_variable, transform)
            transform_function = self._transform_functions[transform]
            exog[transformed_variable_name] = transform_function(exogenous_df[explanatory_variable])

        exog = sm.add_constant(exog)

        return exog

    def add_explanatory_var_transform(self, transform):
        """

        :param transform:
        :return:
        """

        self._check_transform(transform)

        self._explanatory_variable_transform.append(transform)

        self._create_model()

    def get_explanatory_variable(self):
        """

        :return:
        """

        return self._explanatory_variables[0]

    def get_model_formula(self):
        """

        :return:
        """

        if self._response_variable and self._explanatory_variables[0]:

            response_var_transform = self._variable_transform[self._response_variable]
            model_response_var = self._get_variable_transform(self._response_variable, response_var_transform)

            explanatory_variables = []
            for transform in self._explanatory_variable_transform:

                explanatory_variables.append(self._get_variable_transform(self._explanatory_variables[0], transform))

            model_formula = model_response_var + ' ~ ' + ' + '.join(explanatory_variables)

        else:

            model_formula = None

        return model_formula

    def remove_explanatory_var_transform(self, transform):
        """

        :param transform:
        :return:
        """

        if transform in self._explanatory_variable_transform:
            self._explanatory_variable_transform.remove(transform)
            if len(self._explanatory_variable_transform) < 1:
                self._explanatory_variable_transform.append(None)

        self._create_model()

    def reset_explanatory_var_transform(self):
        """

        :return:
        """

        self._explanatory_variable_transform = [None]

        self._create_model()

    def set_explanatory_variable(self, variable):
        """

        :param variable:
        :return:
        """

        self._check_variable_names([variable])
        self._explanatory_variables = (variable,)
        self.update_model()


class CompoundRatingModel(RatingModel):
    """"""

    def __init__(self, data_manager, response_variable=None, explanatory_variable=None):

        super().__init__(data_manager, response_variable)

        self._explanatory_variable_transform = [[None]]

        self._breakpoints = np.array([-np.inf, np.inf])

        self._model = []
        # self._create_model_dataset()

        if explanatory_variable:
            self.set_explanatory_variable(explanatory_variable)
        else:
            self.set_explanatory_variable(data_manager.get_variable_names()[1])

    def _check_segment_number(self, segment_number):
        """

        :param segment:
        :return:
        """

        if not 0 < segment_number and segment_number <= len(self._model):
            raise ValueError("Invalid segment number.")

    def _create_model(self):
        """

        :return:
        """

        self._model = []

        for i in range(self.get_number_of_segments()):
            lower_bound = self._breakpoints[i]
            upper_bound = self._breakpoints[i+1]

            # 12/2/2016
            # switched around compare statement. new version of pandas doesn't like the other way for some reason - MMD
            segment_range_index = (self._model_dataset.ix[:, self._explanatory_variables[0]] >= lower_bound) & \
                                  (self._model_dataset.ix[:, self._explanatory_variables[0]] < upper_bound)

            origin_data = []
            for variable in self._response_variable, self._explanatory_variables[0]:
                for origin in self._data_manager.get_variable_origin(variable):
                    origin_data.append([variable, origin])
            model_data_origin = pd.DataFrame(data=origin_data, columns=['variable', 'origin'])

            segment_data_manager = data.DataManager(self._model_dataset.ix[segment_range_index, :], model_data_origin)

            segment_model = ComplexRatingModel(segment_data_manager,
                                               response_variable=self.get_response_variable(),
                                               explanatory_variable=self.get_explanatory_variable())
            segment_model.exclude_observation(self.get_excluded_observations())
            segment_model.transform_response_variable(self._variable_transform[self._response_variable])

            self._model.append(segment_model)

    def add_breakpoint(self, new_breakpoint):
        """

        :param new_breakpoint:
        :type new_breakpoint: abc.Numeric
        :return:
        """

        breakpoints = np.append(self._breakpoints, new_breakpoint)
        breakpoints = breakpoints[~np.isnan(breakpoints)]
        self._breakpoints = np.sort(breakpoints)

        self.reset_explanatory_var_transform()

        self._create_model()

    def add_explanatory_var_transform(self, transform, segment=None):
        """

        :param segment:
        :param transform:
        :return:
        """

        self._check_transform(transform)

        if segment:
            self._check_segment_number(segment)
            self._model[segment-1].add_explanatory_var_transform(transform)
        else:
            for segment_model in self._model:
                segment_model.add_explanatory_var_transform(transform)

    def get_breakpoints(self):
        """

        :return:
        """

        return copy.deepcopy(self._breakpoints)

    def get_explanatory_variable(self):
        """

        :return:
        """

        return self._explanatory_variables[0]

    def get_model_dataset(self):
        """

        :return:
        """

        model_dataset = pd.DataFrame()

        for i in range(self.get_number_of_segments()):
            segment_model_dataset = self._model[i].get_model_dataset()
            segment_model_dataset['Segment'] = i+1
            model_dataset = pd.concat([model_dataset, segment_model_dataset], verify_integrity=True)

        model_dataset.sort_index(inplace=True)

        return model_dataset

    def get_model_formula(self, segment=None):
        """

        :param segment:
        :return:
        """

        if segment:

            self._check_segment_number(segment)

            model_formula = self._model[segment-1].get_model_formula()

        else:

            model_formula = []

            for segment_model in self._model:

                model_formula.append(segment_model.get_model_formula())

        return model_formula

    def get_model_report(self):
        """

        :return:
        """
        model_report = self._model[0].get_model_report()

        lower_bound = self._float_string_format.format(self._breakpoints[0])
        upper_bound = self._float_string_format.format(self._breakpoints[1])
        report_title = 'Segment model range: ' \
                       + lower_bound \
                       + ' <= ' + self._explanatory_variables[0] \
                       + ' < ' + upper_bound
        model_report.tables[0].title = report_title

        number_of_segments = self.get_number_of_segments()

        spacer_table = SimpleTable(data=['='*50])

        for i in range(1, number_of_segments):
            segment_model_report = self._model[i].get_model_report()
            lower_bound = self._float_string_format.format(self._breakpoints[i])
            upper_bound = self._float_string_format.format(self._breakpoints[i+1])
            report_title = 'Segment model range: ' \
                           + lower_bound \
                           + ' <= ' + self._explanatory_variables[0] \
                           + ' < ' + upper_bound
            segment_model_report.tables[0].title = report_title
            model_report.tables.extend([spacer_table] + segment_model_report.tables)

        return model_report

    def get_model_summary(self):
        """

        :return:
        """

        summary = self._model[0].get_model_summary()
        lower_bound = self._float_string_format.format(self._breakpoints[0])
        upper_bound = self._float_string_format.format(self._breakpoints[1])
        summary_title = 'Segment model range: ' \
                        + lower_bound \
                        + ' <= ' + self._explanatory_variables[0] \
                        + ' < ' + upper_bound
        summary.tables[0].title = summary_title

        number_of_segments = self.get_number_of_segments()

        spacer_table = SimpleTable(data=['='*50])

        for i in range(1, number_of_segments):
            segment_model_summary = self._model[i].get_model_summary()
            lower_bound = self._float_string_format.format(self._breakpoints[i])
            upper_bound = self._float_string_format.format(self._breakpoints[i+1])
            summary_title = 'Segment model range: ' \
                            + lower_bound \
                            + ' <= ' + self._explanatory_variables[0] \
                            + ' < ' + upper_bound
            segment_model_summary.tables[0].title = summary_title
            summary.tables.extend([spacer_table] + segment_model_summary.tables)

        return summary

    def get_number_of_segments(self):
        """

        :return:
        """

        return len(self._breakpoints)-1

    def remove_breakpoint(self, breakpoint):
        """

        :param breakpoint:
        :return:
        """

        new_breakpoints = self._breakpoints[~(self._breakpoints == breakpoint)]
        if np.inf not in new_breakpoints:
            new_breakpoints = np.append(new_breakpoints, np.inf)
        if -np.inf not in new_breakpoints:
            new_breakpoints = np.append(new_breakpoints, -np.inf)

        self._breakpoints = np.sort(new_breakpoints)

        self.reset_explanatory_var_transform()

        self._create_model()

    def remove_explanatory_var_transform(self, transform, segment=None):
        """

        :param segment:
        :param transform:
        :return:
        """

        if segment:
            self._check_segment_number(segment)
            self._model[segment-1].remove_explanatory_var_transform(transform)

        else:

            for segment_model in self._model:
                segment_model.remove_explanatory_var_transform(transform)

    def reset_breakpoints(self):
        """

        :return:
        """

        self._breakpoints = [-np.inf, np.inf]

        self.reset_explanatory_var_transform()

        self._create_model()

    def reset_explanatory_var_transform(self, segment=None):
        """

        :param segment:
        :return:
        """

        if segment:

            self._check_segment_number(segment)

            self._model[segment-1].reset_explanatory_var_transform()

        else:

            for segment_model in self._model:

                segment_model.reset_explanatory_var_transform()

    def set_explanatory_variable(self, explanatory_variable):
        """

        :param explanatory_variable:
        :return:
        """

        self._check_variable_names([explanatory_variable])
        self._explanatory_variables = (explanatory_variable,)

        self.update_model()

    def transform_response_variable(self, transform):
        """

        :param transform:
        :return:
        """

        self._check_transform(transform)

        self._variable_transform[self._response_variable] = transform

        for segment_model in self._model:
            segment_model.transform_response_variable(transform)

    def predict_response_variable(self, explanatory_data=None, bias_correction=False, prediction_interval=False):
        """

        :param explanatory_data:
        :param bias_correction:
        :param prediction_interval:
        :return:
        """

        predicted = pd.DataFrame()

        if explanatory_data:
            explanatory_df = explanatory_data.get_data()
            explanatory_origin = explanatory_data.get_origin()
        else:
            explanatory_df = self._model_dataset.copy(deep=True)
            explanatory_origin = self._model_data_origin.copy(deep=True)

        explanatory_series = explanatory_df[self.get_explanatory_variable()]

        for i in range(self.get_number_of_segments()):
            lower_bound = self._breakpoints[i]
            upper_bound = self._breakpoints[i+1]
            segment_index = (lower_bound <= explanatory_series) & (explanatory_series < upper_bound)
            predictor_data_manager = data.DataManager(explanatory_df.ix[segment_index, :],
                                                      explanatory_origin)
            segment_predicted = self._model[i].predict_response_variable(explanatory_data=predictor_data_manager,
                                                                         bias_correction=bias_correction,
                                                                         prediction_interval=prediction_interval)
            predicted = pd.concat([predicted, segment_predicted])

        return predicted