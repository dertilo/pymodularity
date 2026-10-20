# S3: 16 public top-level names but 1 is decorator-registered, so only
# 15 count against the limit; allowed.
def _register(f):
    return f


@_register
def handler_16():
    pass


name_1 = 1
name_2 = 2
name_3 = 3
name_4 = 4
name_5 = 5
name_6 = 6
name_7 = 7
name_8 = 8
name_9 = 9
name_10 = 10
name_11 = 11
name_12 = 12
name_13 = 13
name_14 = 14
name_15 = 15
