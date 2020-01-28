import logging 
import xarray as xr
import numpy as np

class Model:
    # I/O
    inputNames = []
    inputs = []
    nInputs = 0

    outputNames = None
    outputs = None

    # outputs in an xarray
    xr = None
    def __init__(self, name, description = None):
        assert isinstance(name, str), f"name {name} is not a string"
        self.name = name

        logging.info(f"Model {name} created")
    
    def addOutputs(self, t, outputs, outputNames = None):
        # if no names are provided, make up names
        # if outputs is a list
        if outputNames == None and isinstance(outputs, list):
            outputNames = [self.name + "-output-" + str(i) for i in range(len(outputs))]
        elif outputNames == None:
            outputNames = [self.name + "-output"]

        # sanity check
        assert len(outputs) == len(outputNames)
        
        self.outputs = outputs
        self.outputNames = outputNames

        self.outputsToXarray(t, outputs, outputNames)

    def outputsToXarray(self, t, outputs, outputNames):
        # assume
        nNodes = outputs[0].shape[0]
        nodes = list(range(nNodes))
        # print(len(outputs))
        # print(outputs[0].shape)
        # print(outputs[1].shape)
        allOutputsStacked = np.stack(outputs) # What? Where? When?
        self.xr = xr.DataArray(allOutputsStacked, coords=[outputNames, nodes, t], dims=['variable', 'space', 'time'])

