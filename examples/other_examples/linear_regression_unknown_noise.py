#!/bin/python
"""
An example of how to use tupak to perform paramater estimation for
non-gravitational wave data. In this case, fitting a linear function to
data with background Gaussian noise with unknown variance.

"""
from __future__ import division
import tupak
import numpy as np
import matplotlib.pyplot as plt

# A few simple setup steps
tupak.core.utils.setup_logger()
label = 'linear_regression_unknown_noise'
outdir = 'outdir'


# First, we define our "signal model", in this case a simple linear function
def model(time, m, c):
    return time * m + c


# New we define the injection parameters which we make simulated data with
injection_parameters = dict(m=0.5, c=0.2)

# For this example, we'll inject standard Gaussian noise
sigma = 1

# These lines of code generate the fake data. Note the ** just unpacks the
# contents of the injection_paramsters when calling the model function.
sampling_frequency = 10
time_duration = 10
time = np.arange(0, time_duration, 1/sampling_frequency)
N = len(time)
data = model(time, **injection_parameters) + np.random.normal(0, sigma, N)

# We quickly plot the data to check it looks sensible
fig, ax = plt.subplots()
ax.plot(time, data, 'o', label='data')
ax.plot(time, model(time, **injection_parameters), '--r', label='signal')
ax.set_xlabel('time')
ax.set_ylabel('y')
ax.legend()
fig.savefig('{}/{}_data.png'.format(outdir, label))

# Now lets instantiate the built-in GaussianLikelihood, giving it
# the time, data and signal model. Note that, because we do not give it the
# parameter, sigma is unknown and marginalised over during the sampling
likelihood = tupak.core.likelihood.GaussianLikelihood(time, data, model)

priors = {}
priors['m'] = tupak.core.prior.Uniform(0, 5, 'm')
priors['c'] = tupak.core.prior.Uniform(-2, 2, 'c')
priors['sigma'] = tupak.core.prior.Uniform(0, 10, 'sigma')

# And run sampler
result = tupak.run_sampler(
    likelihood=likelihood, priors=priors, sampler='dynesty', npoints=500,
    walks=10, injection_parameters=injection_parameters, outdir=outdir,
    label=label)
result.plot_corner()
print(result)
