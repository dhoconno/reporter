# NIH RePORTER reporter
Like the 80s band Mister Mister, this is the RePORTER reporter. It generates a plot of cumulative grant awards announced in NIH RePORTER by date, comparing the current year to the previous nine. This can identify trends in award disbursement.

It uses the RePORTER API to retrieve all grants by `award_notice_date` and then creates the output graph. This should update weekly, though I'm new to using GitHub Actions.
