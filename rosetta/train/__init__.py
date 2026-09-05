"""Training-data generation and the RFT grader.

Section 6: fine-tune on COMPREHENSION, not modification. Comprehension labels
are deterministic and free -- they need no execution, so thousands of pairs
come out of static analysis in minutes. Verified-modification data was
rejected in section 5 because each example costs a full execution cycle.

Tools buy correctness at inference time; this buys fluency and VistA-specific
familiarity.
"""
