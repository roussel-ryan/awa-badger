import pandas as pd
import torch
from botorch.models import ModelListGP
from botorch.models.transforms import Normalize, Standardize
from gpytorch import Module
from gpytorch.likelihoods import GaussianLikelihood
from gpytorch.priors import GammaPrior

from xopt.generators.bayesian.base_model import ModelConstructor
from xopt.generators.bayesian.models.prior_mean import CustomMean
from xopt.vocs import VOCS


class StandardModelConstructor(ModelConstructor):
    def __init__(self, vocs: VOCS, options):
        super().__init__(vocs, options)
        # get input transform
        self.input_transform = Normalize(
            self.vocs.n_variables, bounds=torch.tensor(self.vocs.bounds)
        )

        # get likelihood
        self.likelihood = None
        if self.options.use_low_noise_prior:
            self.likelihood = GaussianLikelihood(noise_prior=GammaPrior(1.0, 10.0))

        self.input_data = None
        self.objective_data = None
        self.constraint_data = None
        self.tkwargs = {"dtype": torch.double, "device": "cpu"}

    def collect_data(self, data: pd.DataFrame):
        # drop nans
        valid_data = data[
            pd.unique(self.vocs.variable_names + self.vocs.output_names)
        ]

        # get data
        (
            self.input_data,
            self.objective_data,
            self.constraint_data,
        ) = self.vocs.extract_data(valid_data, return_raw=True)

        self.input_transform.to(**self.tkwargs)
        if self.likelihood is not None:
            self.likelihood.to(**self.tkwargs)

    def build_model(self, data: pd.DataFrame, tkwargs: dict = None) -> ModelListGP:
        """construct independent model for each objective and constraint"""
        # overwrite tkwargs if specified
        self.tkwargs = tkwargs or self.tkwargs

        # collect data from dataframe
        self.collect_data(data)

        # build model
        return self.build_standard_model()

    def build_standard_model(self, **model_kwargs):
        pd.options.mode.use_inf_as_na = True
        models = []
        for name in self.vocs.output_names:
            outcome_transform = Standardize(1)
            covar_module = self._get_module(self.options.covar_modules, name)
            mean_module = self._get_module(self.options.mean_modules, name)

            # objective specifics
            if name in self.vocs.objective_names:
                #print(self.objective_data)
                #print(self.objective_data.isna())

                # get training data
                train_X = torch.tensor(
                    self.input_data[~self.objective_data[name].isnull()].to_numpy(),
                    **self.tkwargs)

                train_Y = torch.tensor(
                    self.objective_data[~self.objective_data[name].isnull()][name].to_numpy(), **self.tkwargs
                ).unsqueeze(-1)
                
                #print(train_X, train_Y)


            # constraint specific
            elif name in self.vocs.constraint_names:
                train_X = torch.tensor(
                    self.input_data[~self.constraint_data[name].isnull()].to_numpy(),
                    **self.tkwargs)

                train_Y = torch.tensor(
                    self.constraint_data[~self.constraint_data[name].isnull()][name].to_numpy(), **self.tkwargs
                ).unsqueeze(-1)
                
                #print(train_X, train_Y)

            else:
                raise RuntimeError(
                    "if you are recieving this message there is "
                    "something wrong with vocs"
                )

            if mean_module is not None:
                mean_module = CustomMean(
                    mean_module, self.input_transform, outcome_transform
                )

            models.append(
                self.build_single_task_gp(
                    train_X,
                    train_Y,
                    input_transform=self.input_transform,
                    outcome_transform=outcome_transform,
                    covar_module=covar_module,
                    mean_module=mean_module,
                    likelihood=self.likelihood,
                )
            )

        return ModelListGP(*models)

    @staticmethod
    def _get_module(base, name):
        if isinstance(base, Module):
            return base
        elif isinstance(base, dict):
            return base.get(name)
        else:
            return None
