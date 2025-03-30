# NIH RePORTER reporter
Like the 80s band [Mr. Mister](https://youtu.be/9NDjt4FzFWY?si=Xc0q9_-9YjigdgIX), this is the RePORTER reporter. It generates a plot of cumulative grant awards announced in [NIH RePORTER](https://reporter.nih.gov) by date, comparing the current year to the previous nine. This can identify trends in award disbursement.

It uses the [RePORTER API](https://api.reporter.nih.gov) to retrieve all grants by `award_notice_date` and then creates the output graph. This includes awards that are both newly awarded and non-competing renewals.

Based on a spot check of NIH RePORTER web captures in the [Internet Archive Wayback Machine](https://web.archive.org/web/20241206064251/https://reporter.nih.gov/) data is refreshed on Sundays. Consequently, current year grant awards could be undercounted by up to one week.

I welcome feedback and corrections if you find any mistakes!

*2025-03-09 - when NIH RePORTER is down, the plots from the current and previous month erroneously show **no** grants from these months. This should self-resolve as soon as RePORTER is online during a daily refresh.*

## Now with 100% more federal register
Under the [Federal Advisory Committee Act](https://www.gsa.gov/policy-regulations/policy/federal-advisory-committee-management/legislation-and-regulations/federal-advisory-committee-act) study sections that review NIH grants need to be announced in the [Federal Register](https://www.federalregister.gov) at least 15 days before the study section meets. I parsed the meetings from Federal Register notices and created plots comparing the current year to the previous nine years.

## Latest HHS terminated grants
[TAGGS HHS terminated grant data explorer](https://dhoconno.github.io/reporter/terminated_grants.html)

The [Health and Human Services Tracking Accountability in Government Grants System (TAGGS) website](https://taggs.hhs.gov) has a link to a PDF of awards that have been terminated. The PDF is not easy to explore and does not contain context (e.g,. investigators, keywords) that may be useful for analysis. I created a [CSV](./HHS_Grants_Terminated.csv) with that fetches this information from NIH RePORTER, where available, and uses this to populate the data explorer. Many of the projects that do not have RePORTER information have additional information in TAGGS, however, there is not an API I can use to easily extract this information. If time permits, I might look into putting together a web scraper. 

## Latest Cumulative Award Count Plot
![Cumulative Awards Plot](./nih_awards.png)

[Interactive Version (Award Counts)](https://dhoconno.github.io/reporter/nih_awards.html)

## Latest Cumulative Award Amount Plot

Inspired by [this analysis in the NYT](https://www.nytimes.com/2025/02/14/health/national-institutes-of-health-research-grants.html), I've also added plots of funding amounts to the plots.

![Cumulative Award Amounts Plot](./nih_award_amounts.png)

[Interactive Version (Award Amounts)](https://dhoconno.github.io/reporter/nih_award_amounts.html)

## Latest Federal Register Study Section Announcements Plot
![Cumulative Federal Register Study Section Announcements](./nih_fr_meetings.png)

[Interactive Version (Federal Register Study Section Announcements)](https://dhoconno.github.io/reporter/nih_fr_meetings.html)

## Notes and caveats
- There may be delays between award notices sent to investigators and their appearance in RePORTER, so that may introduce artifactual lag for the most recent days.
- I am not an expert on NIH RePORTER and rely on `award_notice_date` as an indicator. There may be subtleties to how RePORTER works that distort these results.
- If there are flatlines for any month, please let me know so I can check for an error in the API call results.
- I'm a virologist, not a proper data scientist. So I welcome any and all constructive feedback from those who find problems or areas of improvement.
- I'm spot checking the results but there is no guarantee of accuracy.
- I added a CSV output that shows the underlying data used for the RePORTER analyses.
- Award amounts are not adjusted for inflation
- I added Institute-specific plots. If you see any issues with the underlying parsing _please_ let me know.

## Institute-Specific Plots

The following NIH Institutes and Centers have individual plots available. Click on any link to view the corresponding interactive visualization.

| Institute | Available Plots |
| --- | --- |
| Center for Scientific Review | <span style='color:gray'>awards</span> \| <span style='color:gray'>award amounts</span> \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_CSR.html) |
| Eunice Kennedy Shriver National Institute of Child Health and Human Development | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NICHD.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NICHD.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NICHD.html) |
| Fogarty International Center | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_FIC.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_FIC.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_FIC.html) |
| National Cancer Institute | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NCI.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NCI.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NCI.html) |
| National Center for Advancing Translational Sciences | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NCATS.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NCATS.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NCATS.html) |
| National Eye Institute | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NEI.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NEI.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NEI.html) |
| National Heart, Lung, and Blood Institute | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NHLBI.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NHLBI.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NHLBI.html) |
| National Human Genome Research Institute | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NHGRI.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NHGRI.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NHGRI.html) |
| National Institute of Allergy and Infectious Diseases | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIAID.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIAID.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIAID.html) |
| National Institute of Arthritis and Musculoskeletal and Skin Diseases | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIAMS.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIAMS.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIAMS.html) |
| National Institute of Biomedical Imaging and Bioengineering | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIBIB.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIBIB.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIBIB.html) |
| National Institute of Dental and Craniofacial Research | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIDCR.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIDCR.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIDCR.html) |
| National Institute of Diabetes and Digestive and Kidney Diseases | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIDDK.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIDDK.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIDDK.html) |
| National Institute of Environmental Health Sciences | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIEHS.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIEHS.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIEHS.html) |
| National Institute of General Medical Sciences | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIGMS.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIGMS.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIGMS.html) |
| National Institute of Mental Health | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIMH.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIMH.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIMH.html) |
| National Institute of Neurological Disorders and Stroke | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NINDS.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NINDS.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NINDS.html) |
| National Institute of Nursing Research | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NINR.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NINR.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NINR.html) |
| National Institute on Aging | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIA.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIA.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIA.html) |
| National Institute on Alcohol Abuse and Alcoholism | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIAAA.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIAAA.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIAAA.html) |
| National Institute on Drug Abuse | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIDA.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIDA.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIDA.html) |
| National Institute on Minority Health and Health Disparities | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NIMHD.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NIMHD.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NIMHD.html) |
| National Library of Medicine | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_NLM.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_NLM.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_NLM.html) |
| Office of the Director | [awards](https://dhoconno.github.io/reporter/ic_plots/nih_awards_OD.html) \| [award amounts](https://dhoconno.github.io/reporter/ic_plots/nih_award_amounts_OD.html) \| [meeting notices](https://dhoconno.github.io/reporter/ic_plots/nih_fr_meetings_OD.html) |

*Data last updated: March 20, 2025*

## Methodology (generated by Claude Sonnet 3.7)

The NIH grant award visualization script retrieves funding data directly from the NIH RePORTER API using a day-by-day accumulation methodology. The script queries the API for all grants with award notices issued between the start of each calendar year and the most recent Monday, going back ten years from the current year. The system implements a seven-day caching mechanism to minimize API calls while ensuring data freshness, with current-month data always being refreshed. The script generates two cumulative plots: one for award counts and another for award amounts, both following year-to-date (YTD) progression. The visualization distinguishes the current year with a solid red line, while previous years appear in pastel colors with dashed lines. All raw data is also exported to a single comprehensive CSV file containing year, date, cumulative metrics, and IC-specific breakdowns, then compressed using zstandard at maximum compression level to conserve space while maintaining analytical accessibility.

The Federal Register meeting analysis script systematically extracts and visualizes "Notice of Closed Meeting" announcements published by NIH institutes. The methodology involves querying the Federal Register's API for NIH documents containing specific title patterns related to closed meetings, processing publications from the start of each calendar year through the current date. The script downloads and parses each document's content using available XML, raw text, or HTML formats, applying regular expression patterns to extract meeting details including committee names, dates, and associated institutes. A comprehensive caching system captures both search results and document content to improve efficiency and reduce server load. The cumulative visualization plots the running total of closed meeting announcements by publication date for multiple years, with the current year highlighted in red and previous years in pastel colors. Unlike the NIH grant award script which uses weekly cutoffs, this analysis includes data through the current day to reflect the Federal Register's daily update schedule. The finished visualization maintains consistent dimensions (1200x800 pixels) and resolution with the grant award plots for visual coherence. All extracted meeting data is compiled into a single CSV file containing publication dates, committee names, meeting dates, and institute information, then compressed using zstandard compression to optimize storage while preserving all details necessary for reproduction or further analysis.

## Acknowledgements
- ChatGPT o3-mini-high and Claude Sonnet assisted with deciphering the RePORTER API format and preparing the plots and GitHub Actions.
- Claude Sonnet 3.7 wrote the code for plotting insets, which is much fancier than I know how to do myself
