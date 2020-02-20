import logging
import xarray as xr
import numpy as np

from neurolib.models import bold

from neurolib.utils.collections import dotdict


class Model:
    """The Model superclass manages inputs and outputs of all models.
    """

    def __init__(
        self,
        integration,
        params,
        state_vars,
        init_vars=None,
        output_vars=None,
        input_vars=None,
        default_output=None,
        simulate_bold=False,
        normalize_bold_input=False,
        normalize_bold_input_max=50,
        name=None,
        description=None,
    ):
        if name is not None:
            assert isinstance(name, str), f"Model name is not a string."
            self.name = name

        self.integration = integration

        assert isinstance(params, dict), "Parameters must be a dictionary."
        self.params = dotdict(params)

        self.state_vars = state_vars
        self.init_vars = init_vars
        self.output_vars = output_vars
        self.input_vars = input_vars

        # possibly redundant
        self.default_output = default_output
        self.setDefaultOutput(default_output)

        # create output and state dictionary
        self.outputs = dotdict({})
        self.state = dotdict({})

        # set up bold model
        self.bold_initialized = False
        if simulate_bold:
            self.normalize_bold_input = normalize_bold_input
            self.normalize_bold_input_max = normalize_bold_input_max
            if self.normalize_bold_input:
                logging.info(f"{name}: BOLD input will be normalized to a maxmimum of {normalize_bold_input_max} Hz")

            self.boldModel = bold.BOLDModel(
                self.params["N"], self.params["dt"], normalize_bold_input, normalize_bold_input_max
            )
            self.bold_initialized = True
            logging.info(f"{name}: BOLD model initialized.")

        logging.info(f"{name}: Model initialized.")

    def run(self, chunkwise=False, chunksize=10000, simulate_bold=False, append_outputs=False):
        if chunkwise is False:
            self.integrate()
        else:
            self.integrate_chunkwise(chunksize=chunksize, simulate_bold=simulate_bold, append_outputs=append_outputs)

    def integrate(self, append_outputs=False):
        t, *variables = self.integration(self.params)

        # save time array
        self.setOutput("t", t, append=append_outputs)
        self.setStateVariables("t", t)
        # save outputs
        for svn, sv in zip(self.state_vars, variables):
            if svn in self.output_vars:
                self.setOutput(svn, sv, append=append_outputs)
            self.setStateVariables(svn, sv)

    def integrate_chunkwise(self, chunksize, simulate_bold=False, append_outputs=False):
        totalDuration = self.params["duration"]

        dt = self.params["dt"]
        # create a shallow copy of the parameters
        lastT = 0
        while lastT < totalDuration:
            # Determine the size of the next chunk
            currentChunkSize = min(chunksize, (totalDuration - lastT) / dt)
            currentChunkSize += self.getMaxDelay() + 1

            self.autochunk(duration=currentChunkSize * dt, append_outputs=append_outputs)

            if simulate_bold and self.bold_initialized:
                self.boldModel.run(self.state[self.default_output])
                t_BOLD = self.boldModel.t_BOLD
                BOLD = self.boldModel.BOLD
                self.setOutput("BOLD.t", t_BOLD)
                self.setOutput("BOLD.BOLD", BOLD)

            # we save the last simulated time step
            lastT += self.state["t"][-1]

    def autochunk(self, inputs=None, duration=None, append_outputs=False):
        startindt = self.getMaxDelay() + 1
        if duration is not None:
            chunkDuration = duration
        else:
            chunkDuration = startindt * self.params["dt"] + self.params["dt"]
        self.params["duration"] = chunkDuration
        # set inputs
        if inputs is not None:
            for i, iv in enumerate(self.input_vars):
                self.params[iv] = inputs[i]

        self.integrate(append_outputs=append_outputs)

        # reset initial conditions to last state
        for iv, sv in zip(self.init_vars, self.state_vars):
            # if output variables are one-dimensional (in space only)
            if len(self.state[sv].shape) == 1:
                self.params[iv] = self.state[sv]
            # if they are space-time arrays
            else:
                # we set the next initial condition to the output
                self.params[iv] = self.state[sv][:, -startindt:]

    def getMaxDelay(self):
        """Needs to over overloaded. Maxmimum delay in units of dt!
        
        :return: [description]
        :rtype: [type]
        """
        return 0

    def setStateVariables(self, name, data):
        self.state[name] = data.copy()

    def setOutput(self, name, data, append=False, remove_ics=True):
        """Adds an output to the model, typically a simulation result.
        :params name: Name of the output in dot.notation, a la "outputgroup.output"
        :type name: str
        :params data: Output data, can't be a dictionary!
        :type data: `numpy.ndarray`
        """
        assert not isinstance(data, dict), "Output data cannot be a dictionary."
        assert isinstance(name, str), "Output name must be a string."

        # if the output is a single name (not dot.separated)
        if "." not in name:
            # append data
            if append and name in self.outputs:
                if isinstance(self.outputs[name], np.ndarray):
                    assert isinstance(data, np.ndarray), "Cannot append output, not the old type np.ndarray."
                    # remove initial conditions from data
                    if remove_ics:
                        startindt = self.getMaxDelay() + 1
                        # if data is one-dim (for example time array)
                        if len(data.shape) == 1:
                            # cut off initial condition
                            data = data[startindt:].copy()
                            # if data is a time array, we need to treat it specially
                            # and increment the time by the last recorded duration
                            if name == "t":
                                data += self.outputs[name][-1] - (startindt - 1) * self.params["dt"]
                        elif len(data.shape) == 2:
                            data = data[:, startindt:].copy()
                        else:
                            raise ValueError("Don't know how to truncate data.")
                    self.outputs[name] = np.hstack((self.outputs[name], data))
                # if isinstance(self.outputs[name], list):
                #     assert isinstance(data, np.ndarray), "Cannot append output, not the old type list."
                #     self.outputs[name] = self.outputs[name] + data
                else:
                    raise TypeError(
                        f"Previous output {name} if of type {type(self.outputs[name])}. I can't append to it."
                    )
            else:
                # save all data into output dict
                self.outputs[name] = data
            # set output as an attribute
            setattr(self, name, self.outputs[name])
        else:
            # build results dictionary and write into self.outputs
            # dot.notation iteration
            keys = name.split(".")
            level = self.outputs  # not copy, reference!
            for i, k in enumerate(keys):
                # if it's the last iteration, store data
                if i == len(keys) - 1:
                    # todo: this needs to be append-aware like above
                    # todo: for dotted outputs
                    level[k] = data
                # if key is in outputs, then go deeper
                elif k in level:
                    level = level[k]
                    setattr(self, k, level)
                # if it's a new key, create new nested dictionary, set attribute, then go deeper
                else:
                    level[k] = dotdict({})
                    setattr(self, k, level[k])
                    level = level[k]

    def getOutput(self, name):
        """Get an output of a given name (dot.semarated)
        :param name: A key, grouped outputs in the form group.subgroup.variable
        :type name: str

        :returns: Output data
        """
        assert isinstance(name, str), "Output name must be a string."
        keys = name.split(".")
        lastOutput = self.outputs.copy()
        for i, k in enumerate(keys):
            assert k in lastOutput, f"Key {k} not found in outputs."
            lastOutput = lastOutput[k]
        return lastOutput

    def __getitem__(self, key):
        """Index outputs with a dictionary-like key
        """
        return self.getOutput(key)

    def getOutputs(self, group=""):
        """Get all outputs of an output group. Examples: `getOutputs("BOLD")` or simply `getOutputs()`

        :param group: Group name, subgroups separated by dots. If left empty (default), all outputs of the root group
            are returned.
        :type group: str
        """
        assert isinstance(group, str), "Group name must be a string."

        def filterOutputsFromGroupDict(groupDict):
            """Return a dictionary with the output data of a group disregarding all other nested dicts.
            :param groupDict: Dictionary of outputs (can include other groups)
            :type groupDict: dict
            """
            assert isinstance(groupDict, dict), "Not a dictionary."
            # make a deep copy of the dictionary
            returnDict = groupDict.copy()
            for key, value in groupDict.items():
                if isinstance(value, dict):
                    del returnDict[key]
            return returnDict

        # if a group deeper than the root is given, select the last node
        lastOutput = self.outputs.copy()
        if len(group) > 0:
            keys = group.split(".")
            for i, k in enumerate(keys):
                assert k in lastOutput, f"Key {k} not found in outputs."
                lastOutput = lastOutput[k]
                assert isinstance(lastOutput, dict), f"Key {k} does not refer to a group."
        # filter out all output *groups* that might be in this node and return only output data
        return filterOutputsFromGroupDict(lastOutput)

    def setDefaultOutput(self, name):
        """Sets the default output of the model.
        :param name: Name of the default output.
        :type name: str
        """
        assert isinstance(name, str), "Default output name must be a string."
        self.defaultOutput = name

    def getDefaultOutput(self):
        """Returns value of default output.
        """
        assert self.defaultOutput is not None, "Default output has not been set yet. Use `setDefaultOutput()`."
        return self.getOutput(self.defaultOutput)

    def xr(self, group=""):
        """Converts a group of outputs to xarray. Output group needs to contain an
        element that starts with the letter "t" or it will not recognize any time axis.

        :param group: Output group name, example:  "BOLD". Leave empty for top group.
        :type group: str
        """
        assert isinstance(group, str), "Group name must be a string."
        # take all outputs of one group: disregard all dictionaries because they are subgroups
        outputDict = self.getOutputs(group)
        # make sure that there is a time array
        timeDictKey = ""
        if "t" in outputDict:
            timeDictKey = "t"
        else:
            for k in outputDict:
                if k.startswith("t"):
                    timeDictKey = k
                    logging.info(f"Assuming {k} to be the time axis.")
                    break
        assert len(timeDictKey) > 0, f"No time array found (starting with t) in output group {group}."
        t = outputDict[timeDictKey].copy()
        del outputDict[timeDictKey]
        outputs = []
        outputNames = []
        for key, value in outputDict.items():
            outputNames.append(key)
            outputs.append(value)

        nNodes = outputs[0].shape[0]
        nodes = list(range(nNodes))
        allOutputsStacked = np.stack(outputs)  # What? Where? When?
        result = xr.DataArray(allOutputsStacked, coords=[outputNames, nodes, t], dims=["output", "space", "time"])
        return result
