# Reference-HSQC acceptance protocol

## Evidence and preregistration status

An initial protocol was written before the exploratory literature-fixture
run. That run is now explicitly **SUPERSEDED**: record-level source/rights
were not established and the quercetin structure was not independently verified.
`SUPERSEDED-EXPLORATORY.json` retains the negative observation that 0/3
constructed isomer-mix cases triggered a flag. It is not measured validation.
The initial isomer-mix expectation was an engineering hypothesis, not ground truth.

This revised protocol is declared before executing the synthetic runner revision.
It replaces the unsafe library-dependent sensitivity analysis with explicit
software contracts. The old results must not be quoted as identification accuracy.

## Executable software contracts

`run_evaluation.py` constructs two formula-compatible real RDKit structures
(ethanol and dimethyl ether) and **invented** numeric reference coordinates.
Coordinates are deliberately unrelated to chemical assignments; the runtime's
required `experimental` literal is documented as a test-only curator assertion.
Nothing in this fixture is a measured spectrum.

Six predefined cases: exact reference, small coordinate shift, one-peak query,
duplicate coordinates, absent formula, and wholly nonoverlapping query.
Expected dispositions are fixed in source before execution. Denominator includes
all six cases; exceptions are failures. Report only disposition contract counts,
full outputs, errors, input/output hashes, protocol/runner hashes, and code revision.
No tuning, scientific accuracy, or generalization claims are permitted.

Run twice and compare `RESULTS.json` bytes. Provenance and package versions are
included in each full output. All data and keys are public software fixtures.

## Independent measured evaluation — blocked, not completed

Required prerequisites: rights-cleared independently acquired query HSQC and
measured reference spectra, stable source identifiers, independently verified
structures and assignment provenance, acquisition conditions, permitted local
use and redistribution/external inference where relevant. Owner-held spectra
require author publication permission; public access alone is not permission.

Before viewing held-out outcomes, freeze eligible cases/exclusions, formula-based
candidate construction, data split/leakage controls, H/C tolerances, tie/abstention
rules, missing-response accounting, and evaluator-only keys outside source control.
Include present/absent compounds, close alternatives, incomplete/noisy spectra,
and mismatched acquisition conditions where data support them. Primary metrics:
correct supported preference, incorrect supported preference, abstention and
coverage, stratified by case class; report denominators, failures and uncertainty.
Software checks and reference self-matches are separate evidence layers.

No independent measured-query evaluation or external replication has been run.
No expert review has occurred. The reviewing expert must adjudicate assignment
integrity, structural alternatives, acquisition compatibility, and the scientific
suitability of any proposed decision rule before such claims can be made.
