# A green run with the wrong conclusion

A data import exits successfully and writes the expected number of rows. A sampled
record has its start and end dates reversed. The agent proposes rerunning every
test suite because the green exit cannot be trusted. The relevant parsing code
and a small deterministic fixture are available; the rest of the product was not
changed.

What should the agent investigate and check next? Explain what the successful run
does establish, what it does not establish, and what evidence would support the
actual repair.
