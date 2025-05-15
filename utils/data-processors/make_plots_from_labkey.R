#!/usr/bin/env Rscript
# ------------------------------------------------------------------
#  NIH Awards Dashboard Generator
#  ---------------------------------------------------------------
#  Author: Lab Team (Dave O'Connor et al.)
#  Created: 2025‑05‑15
# 
#  DESCRIPTION
#  This script downloads NIH RePORTER award data from a LabKey list,
#  calculates calendar‑year and fiscal‑year totals (counts and
#  inflation‑adjusted dollar amounts), produces six publication‑ready
#  plots, and optionally uploads the PNG outputs to a LabKey WebDAV
#  folder.  It is designed for unattended, scheduled execution inside
#  GitHub Actions but can also be run locally via:
#
#     Rscript scripts/nih_awards_dashboard.R \
#             --filter "project_num,CONTAINS,AI" \
#             --output  custom.png
#
#  ENVIRONMENT VARIABLES (supplied as GitHub Secrets)
#     * LABKEY_API_KEY  – user‑specific LabKey session key
#     * LABKEY_USER     – LabKey username for WebDAV
#     * LABKEY_PASSWORD – accompanying password
#
#  OUTPUT
#     nih_awards_dashboard.png      (main 6‑panel figure)
#     nih_awards_dashboard.log      (execution log)
#
# ------------------------------------------------------------------

suppressPackageStartupMessages({
  install_and_load <- function(pkgs) {
    missing <- pkgs[!pkgs %in% rownames(installed.packages())]
    if (length(missing))
      install.packages(missing, repos = 'https://cloud.r-project.org', quiet = TRUE)
    invisible(lapply(pkgs, library, character.only = TRUE, quietly = TRUE))
  }
  install_and_load(c(
    'ggplot2','lubridate','Rlabkey','dplyr','scales','quantmod',
    'gridExtra','grid','optparse','logger','httr'
  ))
})

# ---- logging ---------------------------------------------------------------
log_appender(appender_file('nih_awards_dashboard.log'))
log_layout(layout_glue_colors)
log_threshold(INFO)
log_info('Session started at {format(Sys.time(), "%Y-%m-%d %H:%M:%S")}')


# NIH Awards Analysis Dashboard - Creates six plots in one vertical layout
# ------------------------------------------------------------------------

# Load required libraries

## ——— Parse command-line arguments ——————————————————————————
option_list <- list(
  make_option(c("--filter"), type="character", default=NULL, 
              help="Additional column filter in the format 'column,operator,value'
                   Multiple filters can be provided by separating with semicolons.
                   Example: --filter=\"project_num,CONTAINS,AI;org_name,CONTAINS,NIAID\""),
  make_option(c("--output"), type="character", default="nih_awards_dashboard.png", 
              help="Output file name. Default: nih_awards_dashboard.png"),
  make_option(c("--upload"), action="store_true", default=FALSE,
              help="Upload existing files to WebDAV without regenerating plots"),
  make_option(c("--verbose"), action="store_true", default=FALSE,
              help="Enable verbose WebDAV logging")
)

opt_parser <- OptionParser(option_list=option_list)
opt <- parse_args(opt_parser)

## ——— API key retrieval ————————————————————————————
api_key <- Sys.getenv("LABKEY_API_KEY")
if (api_key == "") {
  stop("Please set your LabKey API key in the LABKEY_API_KEY environment variable")
}

## ----- Common data setup -------- 
today <- Sys.Date()
this_year <- year(today)
start_date <- as.Date(paste0(year(today) - 10, "-10-01"))  # 10 fiscal years
cal_cutoff <- yday(today)
current_fy <- ifelse(month(today) >= 10, year(today) + 1, year(today))
fiscal_start <- as.Date(paste0(year(today) - ifelse(month(today) >= 10, 0, 1), "-10-01"))
fy_cutoff <- as.numeric(difftime(today, fiscal_start, units = "days")) + 1

## ----- CPI inflation data ---------
# Download monthly CPI‑U (seasonally adjusted) from FRED
getSymbols("CPIAUCSL", src = "FRED", from = "2010-01-01", auto.assign = TRUE)

# Calendar year CPI
cal_cpi_df <- data.frame(
  date = index(CPIAUCSL),
  cpi  = as.numeric(CPIAUCSL)
) %>%
  mutate(year = year(date)) %>%
  group_by(year) %>%
  summarise(cpi = mean(cpi, na.rm = TRUE), .groups = "drop")

# Fiscal year CPI (Oct-Sep)
fy_cpi_df <- data.frame(
  date = index(CPIAUCSL),
  cpi  = as.numeric(CPIAUCSL)
) %>%
  mutate(fy = ifelse(month(date) >= 10, year(date) + 1, year(date))) %>%
  group_by(fy) %>%
  summarise(cpi = mean(cpi, na.rm = TRUE), .groups = "drop")

# Latest available CPI
latest_cpi <- last(cal_cpi_df$cpi)

# Create inflation factors
cal_inflation_factors <- cal_cpi_df %>%
  mutate(adj_factor = latest_cpi / cpi) %>%
  select(year, adj_factor)

fy_inflation_factors <- fy_cpi_df %>%
  mutate(adj_factor = latest_cpi / cpi) %>%
  select(fy, adj_factor)

## ----- Data retrieval (single call) ---------
labkey.setDefaults(apiKey = api_key)

# Set up the basic date filters
base_filters <- list(
  c("award_notice_date", "DATE_GTE", format(start_date, "%Y-%m-%d")),
  c("award_notice_date", "DATE_LTE", format(today, "%Y-%m-%d"))
)

# Process additional filters from command line arguments
additional_filters <- list()
if (!is.null(opt$filter)) {
  # Split multiple filters by semicolon
  filter_strings <- unlist(strsplit(opt$filter, ";"))
  
  for (filter_string in filter_strings) {
    # Split each filter into column,operator,value
    filter_parts <- unlist(strsplit(filter_string, ","))
    if (length(filter_parts) == 3) {
      additional_filters <- c(additional_filters, list(filter_parts))
    } else {
      warning(paste("Ignoring invalid filter:", filter_string))
    }
  }
}

# Combine all filters
all_filters <- c(base_filters, additional_filters)

# Apply the filters to the query
data <- labkey.selectRows(
    baseUrl    = "https://dholk.primate.wisc.edu",
    folderPath = "/dho/public/reporter",
    schemaName = "lists",
    queryName  = "nih_reporter",
    colSelect  = c("award_notice_date", "award_amount", "org_state", "administering_ic", "project_terms"),
    colFilter  = do.call(makeFilter, all_filters)
)

# Clean up column names
names(data) <- tolower(gsub(" ", "_", names(data)))

## ----- Data Processing ---------
# Convert amounts to numeric
data$award_amount <- as.numeric(data$award_amount)

# Create base data with both calendar and fiscal year information
processed_data <- data %>%
  filter(!is.na(award_notice_date)) %>%
  mutate(
    cal_year = year(award_notice_date),
    cal_doy = yday(award_notice_date),
    fy = ifelse(month(award_notice_date) >= 10, year(award_notice_date) + 1, year(award_notice_date)),
    fy_day = as.numeric(difftime(
      award_notice_date, 
      as.Date(paste0(year(award_notice_date) - ifelse(month(award_notice_date) >= 10, 0, 1), "-10-01")), 
      units = "days"
    )) + 1,
    award_amount = ifelse(is.na(award_amount), 0, award_amount)
  ) %>%
  # Join with inflation factors
  left_join(cal_inflation_factors, by = c("cal_year" = "year")) %>%
  left_join(fy_inflation_factors, by = "fy") %>%
  # Apply inflation adjustments
  mutate(
    adjusted_amount_cal = award_amount * adj_factor.x,
    adjusted_amount_fy = award_amount * adj_factor.y
  )

## ----- Aggregations ---------
# 1. Calendar year awards (counts)
cal_daily_count <- processed_data %>%
  group_by(cal_year, cal_doy) %>%
  summarize(n = n(), .groups = "drop") %>%
  arrange(cal_year, cal_doy) %>%
  group_by(cal_year) %>%
  mutate(cum_n = cumsum(n)) %>%
  ungroup()

# 2. Fiscal year awards (counts)
fy_daily_count <- processed_data %>%
  group_by(fy, fy_day) %>%
  summarize(n = n(), .groups = "drop") %>%
  arrange(fy, fy_day) %>%
  group_by(fy) %>%
  mutate(cum_n = cumsum(n)) %>%
  ungroup()

# 3. Calendar year award amounts
cal_daily_amount <- processed_data %>%
  group_by(cal_year, cal_doy) %>%
  summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
  arrange(cal_year, cal_doy) %>%
  group_by(cal_year) %>%
  mutate(cum_amount = cumsum(amount)) %>%
  ungroup()

# 4. Calendar year inflation-adjusted award amounts
cal_daily_adjusted <- processed_data %>%
  group_by(cal_year, cal_doy) %>%
  summarize(amount = sum(adjusted_amount_cal, na.rm = TRUE), .groups = "drop") %>%
  arrange(cal_year, cal_doy) %>%
  group_by(cal_year) %>%
  mutate(cum_amount = cumsum(amount)) %>%
  ungroup()

# 5. Fiscal year award amounts
fy_daily_amount <- processed_data %>%
  group_by(fy, fy_day) %>%
  summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
  arrange(fy, fy_day) %>%
  group_by(fy) %>%
  mutate(cum_amount = cumsum(amount)) %>%
  ungroup()

# 6. Fiscal year inflation-adjusted award amounts
fy_daily_adjusted <- processed_data %>%
  group_by(fy, fy_day) %>%
  summarize(amount = sum(adjusted_amount_fy, na.rm = TRUE), .groups = "drop") %>%
  arrange(fy, fy_day) %>%
  group_by(fy) %>%
  mutate(cum_amount = cumsum(amount)) %>%
  ungroup()

## ----- Plot Creation Functions ---------
# Helper function to create color palettes
create_color_palette <- function(unique_years, current_year) {
  num_years <- length(unique_years)
  if (num_years == 0) return(c())
  
  year_positions <- match(unique_years, sort(unique_years))
  current_year_position <- match(current_year, sort(unique_years))
  
  pastel_colors <- scales::hue_pal(h.start = 120, l = 80)(num_years)
  
  if (current_year %in% unique_years) {
    pastel_colors[current_year_position] <- "#FF0000"
  }
  
  return(pastel_colors)
}

# 1. Calendar Year Count Plot
create_cal_count_plot <- function(cal_daily_count_data) {
  unique_years <- unique(cal_daily_count_data$cal_year)
  color_values <- create_color_palette(unique_years, this_year)
  
  first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                             as.Date(paste0(this_year, "-12-01")), 
                             by = "month"))
  first_of_months <- first_of_months[first_of_months <= cal_cutoff]
  
  max_y_value <- cal_daily_count_data %>%
    filter(cal_doy <= cal_cutoff) %>%
    pull(cum_n) %>%
    max()
  
  ggplot(cal_daily_count_data,
         aes(x = cal_doy, y = cum_n,
             colour = factor(cal_year),
             size = cal_year == this_year)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, cal_cutoff),
                       breaks = first_of_months,
                       labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                               as.Date(paste0(this_year, "-12-01")), 
                                               by = "month")[1:length(first_of_months)],
                                      "%b %d")) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05)) +
    labs(title = "Cumulative NIH Awards (YTD) by Calendar Year",
         x = "Date",
         y = "Cumulative Award Count") +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          plot.title = element_text(size = 12))
}

# 2. Fiscal Year Count Plot
create_fy_count_plot <- function(fy_daily_count_data) {
  unique_years <- unique(fy_daily_count_data$fy)
  color_values <- create_color_palette(unique_years, current_fy)
  
  month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
  month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
  month_names <- format(month_dates, "%b")
  month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
  month_labels <- month_names[1:length(month_breaks)]
  
  max_y_value <- fy_daily_count_data %>%
    filter(fy_day <= fy_cutoff) %>%
    pull(cum_n) %>%
    max()
  
  ggplot(fy_daily_count_data,
         aes(x = fy_day, y = cum_n,
             colour = factor(fy),
             size = fy == current_fy)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Fiscal Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, fy_cutoff),
                       breaks = month_breaks,
                       labels = month_labels) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05)) +
    labs(title = "Cumulative NIH Awards by Fiscal Year (October 1 - September 30)",
         x = "Month",
         y = "Cumulative Award Count") +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
          plot.title = element_text(size = 12))
}

# 3. Calendar Year Award Plot
create_cal_amount_plot <- function(cal_daily_amount_data) {
  unique_years <- unique(cal_daily_amount_data$cal_year)
  color_values <- create_color_palette(unique_years, this_year)
  
  first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                             as.Date(paste0(this_year, "-12-01")), 
                             by = "month"))
  first_of_months <- first_of_months[first_of_months <= cal_cutoff]
  
  max_y_value <- cal_daily_amount_data %>%
    filter(cal_doy <= cal_cutoff) %>%
    pull(cum_amount) %>%
    max()
  
  ggplot(cal_daily_amount_data,
         aes(x = cal_doy, y = cum_amount,
             colour = factor(cal_year),
             size = cal_year == this_year)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, cal_cutoff),
                       breaks = first_of_months,
                       labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                               as.Date(paste0(this_year, "-12-01")), 
                                               by = "month")[1:length(first_of_months)],
                                      "%b %d")) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
    labs(title = "Cumulative NIH Award Amounts (YTD) by Calendar Year",
         x = "Date",
         y = "Cumulative Amount (Billions $)") +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          plot.title = element_text(size = 12))
}

# 4. Calendar Year Inflation-Adjusted Amount Plot
create_cal_adjusted_plot <- function(cal_daily_adjusted_data) {
  unique_years <- unique(cal_daily_adjusted_data$cal_year)
  color_values <- create_color_palette(unique_years, this_year)
  
  first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                             as.Date(paste0(this_year, "-12-01")), 
                             by = "month"))
  first_of_months <- first_of_months[first_of_months <= cal_cutoff]
  
  max_y_value <- cal_daily_adjusted_data %>%
    filter(cal_doy <= cal_cutoff) %>%
    pull(cum_amount) %>%
    max()
  
  ggplot(cal_daily_adjusted_data,
         aes(x = cal_doy, y = cum_amount,
             colour = factor(cal_year),
             size = cal_year == this_year)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, cal_cutoff),
                       breaks = first_of_months,
                       labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                               as.Date(paste0(this_year, "-12-01")), 
                                               by = "month")[1:length(first_of_months)],
                                      "%b %d")) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
    labs(title = "Inflation-Adjusted Cumulative NIH Award Amounts by Calendar Year",
         subtitle = paste0("All amounts in ", year(today), " dollars"),
         x = "Date",
         y = paste0("Cumulative Amount (Billions $, ", year(today), " dollars)")) +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          plot.title = element_text(size = 12),
          plot.subtitle = element_text(size = 9))
}

# 5. Fiscal Year Amount Plot
create_fy_amount_plot <- function(fy_daily_amount_data) {
  unique_years <- unique(fy_daily_amount_data$fy)
  color_values <- create_color_palette(unique_years, current_fy)
  
  month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
  month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
  month_names <- format(month_dates, "%b")
  month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
  month_labels <- month_names[1:length(month_breaks)]
  
  max_y_value <- fy_daily_amount_data %>%
    filter(fy_day <= fy_cutoff) %>%
    pull(cum_amount) %>%
    max()
  
  ggplot(fy_daily_amount_data,
         aes(x = fy_day, y = cum_amount,
             colour = factor(fy),
             size = fy == current_fy)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Fiscal Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, fy_cutoff),
                       breaks = month_breaks,
                       labels = month_labels) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
    labs(title = "Cumulative NIH Award Amounts by Fiscal Year (October 1 - September 30)",
         x = "Month",
         y = "Cumulative Amount (Billions $)") +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
          plot.title = element_text(size = 12))
}

# 6. Fiscal Year Inflation-Adjusted Amount Plot
create_fy_adjusted_plot <- function(fy_daily_adjusted_data) {
  unique_years <- unique(fy_daily_adjusted_data$fy)
  color_values <- create_color_palette(unique_years, current_fy)
  
  month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
  month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
  month_names <- format(month_dates, "%b")
  month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
  month_labels <- month_names[1:length(month_breaks)]
  
  max_y_value <- fy_daily_adjusted_data %>%
    filter(fy_day <= fy_cutoff) %>%
    pull(cum_amount) %>%
    max()
  
  ggplot(fy_daily_adjusted_data,
         aes(x = fy_day, y = cum_amount,
             colour = factor(fy),
             size = fy == current_fy)) +
    geom_line() +
    scale_colour_manual(values = color_values, name = "Fiscal Year") +
    scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
    scale_x_continuous(limits = c(1, fy_cutoff),
                       breaks = month_breaks,
                       labels = month_labels) +
    scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
    labs(title = "Inflation-Adjusted Cumulative NIH Award Amounts by Fiscal Year",
         subtitle = paste0("All amounts in ", year(today), " dollars"),
         x = "Month",
         y = paste0("Cumulative Amount (Billions $, ", year(today), " dollars)")) +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
          plot.title = element_text(size = 12),
          plot.subtitle = element_text(size = 9))
}

## ----- Create all plots ---------
plot1 <- create_cal_count_plot(cal_daily_count)
plot2 <- create_fy_count_plot(fy_daily_count)
plot3 <- create_cal_amount_plot(cal_daily_amount)
plot4 <- create_cal_adjusted_plot(cal_daily_adjusted)
plot5 <- create_fy_amount_plot(fy_daily_amount)
plot6 <- create_fy_adjusted_plot(fy_daily_adjusted)

## ----- Set up the output file --------
png(opt$output, width = 1600, height = 5400, res = 180)

# Add an overall title and date stamp
title_text <- paste0("NIH Awards Analysis Dashboard - Generated ", format(today, "%B %d, %Y"))
# Add filter info to title if there are additional filters
if (length(additional_filters) > 0) {
  filter_description <- paste(sapply(additional_filters, function(f) paste(f[1], f[2], f[3], sep=" ")), collapse=", ")
  title_text <- paste0(title_text, "\nFilters: ", filter_description)
}

title <- textGrob(
  title_text,
  gp = gpar(fontsize = 18, fontface = "bold")
)

# Arrange the plots vertically
grid.arrange(
  title,
  plot1, plot2, plot3, plot4, plot5, plot6,
  ncol = 1,
  heights = c(0.4, 1, 1, 1, 1, 1, 1)
)

dev.off()
cat(paste0("Dashboard created as ", opt$output, "\n"))

## ----- Generate state-specific plots ---------
generate_state_plots <- function() {
  # Create by_state directory if it doesn't exist
  state_dir <- file.path(dirname(opt$output), "by_state")
  if (!dir.exists(state_dir)) {
    dir.create(state_dir, recursive = TRUE)
  }
  
  # Get unique states (exclude NA values)
  unique_states <- unique(processed_data$org_state)
  unique_states <- unique_states[!is.na(unique_states)]
  
  cat(paste0("Generating plots for ", length(unique_states), " states/territories...\n"))
  
  for (state in unique_states) {
    # Filter data for this state
    state_processed_data <- processed_data %>%
      filter(org_state == state)
    
    if (nrow(state_processed_data) == 0) {
      cat(paste0("No data for state: ", state, ", skipping.\n"))
      next
    }
    
    # Create state aggregations
    # 1. Calendar year awards (counts)
    state_cal_daily_count <- state_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 2. Fiscal year awards (counts)
    state_fy_daily_count <- state_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 3. Calendar year award amounts
    state_cal_daily_amount <- state_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 4. Calendar year inflation-adjusted award amounts
    state_cal_daily_adjusted <- state_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(adjusted_amount_cal, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 5. Fiscal year award amounts
    state_fy_daily_amount <- state_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 6. Fiscal year inflation-adjusted award amounts
    state_fy_daily_adjusted <- state_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(adjusted_amount_fy, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # Create all plots using the state-specific data
    plot1 <- create_cal_count_plot(state_cal_daily_count)
    plot2 <- create_fy_count_plot(state_fy_daily_count)
    plot3 <- create_cal_amount_plot(state_cal_daily_amount)
    plot4 <- create_cal_adjusted_plot(state_cal_daily_adjusted)
    plot5 <- create_fy_amount_plot(state_fy_daily_amount)
    plot6 <- create_fy_adjusted_plot(state_fy_daily_adjusted)
    
    # Set up the output file for this state
    state_output_file <- file.path(state_dir, paste0(state, ".png"))
    png(state_output_file, width = 1600, height = 5400, res = 180)
    
    # Add an overall title with state info
    title_text <- paste0("NIH Awards Analysis Dashboard - State: ", state, " - Generated ", format(today, "%B %d, %Y"))
    
    # Add filter info to title if there are additional filters
    if (length(additional_filters) > 0) {
      filter_description <- paste(sapply(additional_filters, function(f) paste(f[1], f[2], f[3], sep=" ")), collapse=", ")
      title_text <- paste0(title_text, "\nFilters: ", filter_description)
    }
    
    title <- textGrob(
      title_text,
      gp = gpar(fontsize = 18, fontface = "bold")
    )
    
    # Arrange the plots vertically
    grid.arrange(
      title,
      plot1, plot2, plot3, plot4, plot5, plot6,
      ncol = 1,
      heights = c(0.4, 1, 1, 1, 1, 1, 1)
    )
    
    dev.off()
    cat(paste0("Created dashboard for ", state, "\n"))
  }
  
  cat(paste0("State dashboards created in ", state_dir, "\n"))
}

## ----- Generate institute-specific plots ---------
generate_institute_plots <- function() {
  # Create by_institute directory if it doesn't exist
  institute_dir <- file.path(dirname(opt$output), "by_institute")
  if (!dir.exists(institute_dir)) {
    dir.create(institute_dir, recursive = TRUE)
  }
  
  # Get unique institutes (exclude NA values)
  unique_institutes <- unique(processed_data$administering_ic)
  unique_institutes <- unique_institutes[!is.na(unique_institutes)]
  
  cat(paste0("Generating plots for ", length(unique_institutes), " institutes/centers...\n"))
  
  for (institute in unique_institutes) {
    # Filter data for this institute
    institute_processed_data <- processed_data %>%
      filter(administering_ic == institute)
    
    if (nrow(institute_processed_data) == 0) {
      cat(paste0("No data for institute: ", institute, ", skipping.\n"))
      next
    }
    
    # Create institute aggregations
    # 1. Calendar year awards (counts)
    institute_cal_daily_count <- institute_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 2. Fiscal year awards (counts)
    institute_fy_daily_count <- institute_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 3. Calendar year award amounts
    institute_cal_daily_amount <- institute_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 4. Calendar year inflation-adjusted award amounts
    institute_cal_daily_adjusted <- institute_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(adjusted_amount_cal, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 5. Fiscal year award amounts
    institute_fy_daily_amount <- institute_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 6. Fiscal year inflation-adjusted award amounts
    institute_fy_daily_adjusted <- institute_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(adjusted_amount_fy, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # Create all plots using the institute-specific data
    plot1 <- create_cal_count_plot(institute_cal_daily_count)
    plot2 <- create_fy_count_plot(institute_fy_daily_count)
    plot3 <- create_cal_amount_plot(institute_cal_daily_amount)
    plot4 <- create_cal_adjusted_plot(institute_cal_daily_adjusted)
    plot5 <- create_fy_amount_plot(institute_fy_daily_amount)
    plot6 <- create_fy_adjusted_plot(institute_fy_daily_adjusted)
    
    # Set up the output file for this institute
    institute_output_file <- file.path(institute_dir, paste0(institute, ".png"))
    png(institute_output_file, width = 1600, height = 5400, res = 180)
    
    # Add an overall title with institute info
    title_text <- paste0("NIH Awards Analysis Dashboard - Institute/Center: ", institute, " - Generated ", format(today, "%B %d, %Y"))
    
    # Add filter info to title if there are additional filters
    if (length(additional_filters) > 0) {
      filter_description <- paste(sapply(additional_filters, function(f) paste(f[1], f[2], f[3], sep=" ")), collapse=", ")
      title_text <- paste0(title_text, "\nFilters: ", filter_description)
    }
    
    title <- textGrob(
      title_text,
      gp = gpar(fontsize = 18, fontface = "bold")
    )
    
    # Arrange the plots vertically
    grid.arrange(
      title,
      plot1, plot2, plot3, plot4, plot5, plot6,
      ncol = 1,
      heights = c(0.4, 1, 1, 1, 1, 1, 1)
    )
    
    dev.off()
    cat(paste0("Created dashboard for institute: ", institute, "\n"))
  }
  
  cat(paste0("Institute dashboards created in ", institute_dir, "\n"))
}

## ----- Generate condition-specific plots ---------
generate_condition_plots <- function() {
  # Create by_condition directory if it doesn't exist
  condition_dir <- file.path(dirname(opt$output), "by_condition")
  if (!dir.exists(condition_dir)) {
    dir.create(condition_dir, recursive = TRUE)
  }
  
  # Define top 25 conditions with their associated keywords
  condition_keywords <- list(
    heart_disease = c("heart disease", "cardiovascular", "coronary", "heart failure", "myocardial infarction"),
    breast_cancer = c("breast cancer", "mammary carcinoma"),
    lung_cancer = c("lung cancer", "pulmonary cancer", "bronchogenic carcinoma"),
    prostate_cancer = c("prostate cancer"),
    colorectal_cancer = c("colorectal cancer", "colon cancer", "rectal cancer"),
    pancreatic_cancer = c("pancreatic cancer"),
    leukemia = c("leukemia"),
    lymphoma = c("lymphoma"),
    melanoma = c("melanoma"),
    covid = c("covid", "sars-cov-2", "coronavirus"),
    trauma = c("trauma", "injury", "accident"),
    stroke = c("stroke", "cerebrovascular", "brain hemorrhage"),
    respiratory_disease = c("copd", "asthma", "emphysema", "bronchitis", "respiratory disease"),
    alzheimer = c("alzheimer", "dementia", "neurodegenerative"),
    diabetes = c("diabetes", "insulin resistance", "hyperglycemia"),
    influenza = c("influenza", "pneumonia", "flu"),
    kidney_disease = c("kidney disease", "renal disease", "nephropathy", "dialysis"),
    mental_health = c("depression", "anxiety", "mental health", "psychiatric"),
    suicide = c("suicide", "self-harm"),
    liver_disease = c("liver disease", "cirrhosis", "hepatitis"),
    hypertension = c("hypertension", "high blood pressure"),
    parkinson = c("parkinson", "movement disorder"),
    obesity = c("obesity", "overweight"),
    hiv_aids = c("hiv", "aids", "acquired immune deficiency syndrome"),
    opioid = c("opioid", "addiction", "substance abuse")
  )
  
  cat(paste0("Generating plots for ", length(condition_keywords), " health conditions...\n"))
  
  for (condition_name in names(condition_keywords)) {
    cat(paste0("Processing condition: ", condition_name, "...\n"))
    
    # Convert project_terms to lowercase for case-insensitive matching
    processed_data$project_terms_lower <- tolower(processed_data$project_terms)
    
    # Filter data for this condition (match any keyword)
    keywords <- condition_keywords[[condition_name]]
    
    # Initialize an empty data frame for this condition
    condition_processed_data <- data.frame()
    
    # For each keyword, find matching grants and combine them
    for (keyword in keywords) {
      keyword_lower <- tolower(keyword)
      matching_data <- processed_data %>%
        filter(grepl(keyword_lower, project_terms_lower, fixed = TRUE))
      
      # Combine with previous matches
      condition_processed_data <- rbind(condition_processed_data, matching_data)
    }
    
    # Remove duplicates (grants that matched multiple keywords)
    condition_processed_data <- unique(condition_processed_data)
    
    if (nrow(condition_processed_data) == 0) {
      cat(paste0("No data for condition: ", condition_name, ", skipping.\n"))
      next
    }
    
    # Create condition aggregations
    # 1. Calendar year awards (counts)
    condition_cal_daily_count <- condition_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 2. Fiscal year awards (counts)
    condition_fy_daily_count <- condition_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(n = n(), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_n = cumsum(n)) %>%
      ungroup()
    
    # 3. Calendar year award amounts
    condition_cal_daily_amount <- condition_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 4. Calendar year inflation-adjusted award amounts
    condition_cal_daily_adjusted <- condition_processed_data %>%
      group_by(cal_year, cal_doy) %>%
      summarize(amount = sum(adjusted_amount_cal, na.rm = TRUE), .groups = "drop") %>%
      arrange(cal_year, cal_doy) %>%
      group_by(cal_year) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 5. Fiscal year award amounts
    condition_fy_daily_amount <- condition_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # 6. Fiscal year inflation-adjusted award amounts
    condition_fy_daily_adjusted <- condition_processed_data %>%
      group_by(fy, fy_day) %>%
      summarize(amount = sum(adjusted_amount_fy, na.rm = TRUE), .groups = "drop") %>%
      arrange(fy, fy_day) %>%
      group_by(fy) %>%
      mutate(cum_amount = cumsum(amount)) %>%
      ungroup()
    
    # Create all plots using the condition-specific data
    plot1 <- create_cal_count_plot(condition_cal_daily_count)
    plot2 <- create_fy_count_plot(condition_fy_daily_count)
    plot3 <- create_cal_amount_plot(condition_cal_daily_amount)
    plot4 <- create_cal_adjusted_plot(condition_cal_daily_adjusted)
    plot5 <- create_fy_amount_plot(condition_fy_daily_amount)
    plot6 <- create_fy_adjusted_plot(condition_fy_daily_adjusted)
    
    # Set up the output file for this condition
    condition_output_file <- file.path(condition_dir, paste0(condition_name, ".png"))
    png(condition_output_file, width = 1600, height = 5400, res = 180)
    
    # Format keywords for display
    keywords_display <- paste(keywords, collapse = ", ")
    
    # Add an overall title with condition info
    title_text <- paste0("NIH Awards Analysis Dashboard - Condition: ", 
                         gsub("_", " ", condition_name), 
                         " (Keywords: ", keywords_display, ") - Generated ",
                         format(today, "%B %d, %Y"))
    
    # Add filter info to title if there are additional filters
    if (length(additional_filters) > 0) {
      filter_description <- paste(sapply(additional_filters, function(f) paste(f[1], f[2], f[3], sep=" ")), collapse=", ")
      title_text <- paste0(title_text, "\nFilters: ", filter_description)
    }
    
    # Add the count of grants that matched
    title_text <- paste0(title_text, "\nMatched ", nrow(condition_processed_data), " grants")
    
    title <- textGrob(
      title_text,
      gp = gpar(fontsize = 18, fontface = "bold")
    )
    
    # Arrange the plots vertically
    grid.arrange(
      title,
      plot1, plot2, plot3, plot4, plot5, plot6,
      ncol = 1,
      heights = c(0.5, 1, 1, 1, 1, 1, 1)
    )
    
    dev.off()
    cat(paste0("Created dashboard for condition: ", condition_name, 
               " (", nrow(condition_processed_data), " grants matched)\n"))
  }
  
  cat(paste0("Condition dashboards created in ", condition_dir, "\n"))
}

## ----- Upload files via WebDAV --------
upload_files_to_webdav <- function() {
  # Make sure the httr package is available
  if (!requireNamespace("httr", quietly = TRUE)) {
    install.packages("httr")
  }
  
  # Base settings
  webdav_base_url <- "https://dholk.primate.wisc.edu/_webdav/dho/public/reporter/@files/"
  
  # Get credentials - either from environment variables or prompt user
  webdav_user <- Sys.getenv("LABKEY_USER")
  webdav_pass <- Sys.getenv("LABKEY_PASSWORD")
  
  if (webdav_user == "" || webdav_pass == "") {
    cat("LabKey credentials not found in environment variables.\n")
    webdav_user <- readline("Enter LabKey username: ")
    webdav_pass <- readline("Enter LabKey password: ")
  }
  
  cat("WebDAV: Using credentials for user:", webdav_user, "\n")
  
  # Test connection
  test_url <- paste0(webdav_base_url)
  cat("WebDAV: Testing connection to", test_url, "\n")
  
  test_response <- httr::GET(
    test_url,
    authenticate(webdav_user, webdav_pass)
  )
  
  if (httr::status_code(test_response) >= 400) {
    stop(paste("WebDAV: Connection failed with status code:", 
              httr::status_code(test_response), 
              "\nResponse:", httr::content(test_response, "text", encoding = "UTF-8")))
  } else {
    cat("WebDAV: Connection successful (status code:", httr::status_code(test_response), ")\n")
  }
  
  # Function to create directory if it doesn't exist
  ensure_webdav_dir <- function(dir_path) {
    dir_url <- paste0(webdav_base_url, dir_path)
    cat("WebDAV: Creating directory:", dir_path, "\n")
    
    if (opt$verbose) {
      response <- httr::VERB(
        "MKCOL",
        url = dir_url,
        authenticate(webdav_user, webdav_pass),
        verbose()
      )
    } else {
      response <- httr::VERB(
        "MKCOL",
        url = dir_url,
        authenticate(webdav_user, webdav_pass)
      )
    }
    
    # Status code 201 (Created) or 405 (Method Not Allowed, often means dir exists)
    if (httr::status_code(response) == 201) {
      cat("WebDAV: Directory created successfully:", dir_path, "\n")
      return(TRUE)
    } else if (httr::status_code(response) == 405) {
      cat("WebDAV: Directory already exists:", dir_path, "\n")
      return(TRUE)
    } else {
      warning(paste("WebDAV: Failed to create directory:", dir_path, 
                   "Status:", httr::status_code(response)))
      return(FALSE)
    }
  }
  
  # Function to upload a file
  upload_file <- function(local_path, remote_path) {
    if (!file.exists(local_path)) {
      warning(paste("WebDAV: File not found:", local_path))
      return(FALSE)
    }
    
    remote_url <- paste0(webdav_base_url, remote_path)
    file_size <- file.info(local_path)$size
    file_contents <- readBin(local_path, "raw", n = file_size)
    
    cat(sprintf("WebDAV: Uploading file %s (%.2f KB) to %s (will overwrite if exists)\n", 
               basename(local_path), 
               file_size/1024, 
               remote_path))
    
    start_time <- Sys.time()
    
    # Add headers to ensure overwriting existing files
    headers <- httr::add_headers(
      "Overwrite" = "T",           # Explicitly request overwrite 
      "If-Match" = "*"             # Match any existing version (force overwrite)
    )
    
    if (opt$verbose) {
      response <- httr::PUT(
        remote_url,
        authenticate(webdav_user, webdav_pass),
        body = file_contents,
        headers,
        verbose()
      )
    } else {
      response <- httr::PUT(
        remote_url,
        authenticate(webdav_user, webdav_pass),
        body = file_contents,
        headers
      )
    }
    end_time <- Sys.time()
    
    time_taken <- difftime(end_time, start_time, units = "secs")
    
    if (httr::status_code(response) %in% c(200, 201, 204)) {
      cat(sprintf("WebDAV: Successfully uploaded %s to %s (%.2f KB in %.1f seconds)\n", 
                 basename(local_path), remote_path, file_size/1024, time_taken))
      return(TRUE)
    } else {
      warning(sprintf("WebDAV: Failed to upload %s - Status: %d\nResponse: %s\n", 
                     basename(local_path), 
                     httr::status_code(response),
                     httr::content(response, "text", encoding = "UTF-8")))
      return(FALSE)
    }
  }
  
  # Get base directory from output path
  base_dir <- dirname(opt$output)
  
  # Create base directories
  ensure_webdav_dir("plots")
  ensure_webdav_dir("plots/by_state")
  ensure_webdav_dir("plots/by_institute")
  ensure_webdav_dir("plots/by_condition")
  
  # Upload main dashboard if it exists
  if (file.exists(opt$output)) {
    upload_file(opt$output, paste0("plots/", basename(opt$output)))
  } else {
    cat(paste0("WebDAV: Main dashboard file not found: ", opt$output, "\n"))
  }
  
  # Upload state plots
  state_dir <- file.path(base_dir, "by_state")
  if (dir.exists(state_dir)) {
    state_files <- list.files(state_dir, pattern = "\\.png$", full.names = TRUE)
    cat(paste0("WebDAV: Found ", length(state_files), " state plot files to upload.\n"))
    for (file in state_files) {
      upload_file(file, paste0("plots/by_state/", basename(file)))
    }
  } else {
    cat(paste0("WebDAV: State directory not found: ", state_dir, "\n"))
  }
  
  # Upload institute plots
  institute_dir <- file.path(base_dir, "by_institute")
  if (dir.exists(institute_dir)) {
    institute_files <- list.files(institute_dir, pattern = "\\.png$", full.names = TRUE)
    cat(paste0("WebDAV: Found ", length(institute_files), " institute plot files to upload.\n"))
    for (file in institute_files) {
      upload_file(file, paste0("plots/by_institute/", basename(file)))
    }
  } else {
    cat(paste0("WebDAV: Institute directory not found: ", institute_dir, "\n"))
  }
  
  # Upload condition plots
  condition_dir <- file.path(base_dir, "by_condition")
  if (dir.exists(condition_dir)) {
    condition_files <- list.files(condition_dir, pattern = "\\.png$", full.names = TRUE)
    cat(paste0("WebDAV: Found ", length(condition_files), " condition plot files to upload.\n"))
    for (file in condition_files) {
      upload_file(file, paste0("plots/by_condition/", basename(file)))
    }
  } else {
    cat(paste0("WebDAV: Condition directory not found: ", condition_dir, "\n"))
  }
  
  cat("WebDAV: Upload completed.\n")
}

# Main execution flow - COMPLETE REPLACEMENT for the end of your script
# Main execution branch - do only what's needed based on the mode
if (opt$upload) {
  cat("Upload mode: Uploading existing files to WebDAV without regenerating plots.\n")
  # Skip all data retrieval, processing, and plot generation
  upload_files_to_webdav()
} else {
  # Full processing mode
  cat("Processing mode: Retrieving data, generating and uploading plots.\n")
  
  # API key retrieval
  api_key <- Sys.getenv("LABKEY_API_KEY")
  if (api_key == "") {
    stop("Please set your LabKey API key in the LABKEY_API_KEY environment variable")
  }
  
  # Set up common variables
  today <- Sys.Date()
  this_year <- year(today)
  start_date <- as.Date(paste0(year(today) - 10, "-10-01"))  # 10 fiscal years
  cal_cutoff <- yday(today)
  current_fy <- ifelse(month(today) >= 10, year(today) + 1, year(today))
  fiscal_start <- as.Date(paste0(year(today) - ifelse(month(today) >= 10, 0, 1), "-10-01"))
  fy_cutoff <- as.numeric(difftime(today, fiscal_start, units = "days")) + 1
  
  # Download CPI data (now only happens in processing mode)
  getSymbols("CPIAUCSL", src = "FRED", from = "2010-01-01", auto.assign = TRUE)
  
  # Calendar year CPI
  cal_cpi_df <- data.frame(
    date = index(CPIAUCSL),
    cpi  = as.numeric(CPIAUCSL)
  ) %>%
    mutate(year = year(date)) %>%
    group_by(year) %>%
    summarise(cpi = mean(cpi, na.rm = TRUE), .groups = "drop")
  
  # Fiscal year CPI (Oct-Sep)
  fy_cpi_df <- data.frame(
    date = index(CPIAUCSL),
    cpi  = as.numeric(CPIAUCSL)
  ) %>%
    mutate(fy = ifelse(month(date) >= 10, year(date) + 1, year(date))) %>%
    group_by(fy) %>%
    summarise(cpi = mean(cpi, na.rm = TRUE), .groups = "drop")
  
  # Latest available CPI
  latest_cpi <- last(cal_cpi_df$cpi)
  
  # Create inflation factors
  cal_inflation_factors <- cal_cpi_df %>%
    mutate(adj_factor = latest_cpi / cpi) %>%
    select(year, adj_factor)
  
  fy_inflation_factors <- fy_cpi_df %>%
    mutate(adj_factor = latest_cpi / cpi) %>%
    select(fy, adj_factor)
  
  # Data retrieval and processing
  labkey.setDefaults(apiKey = api_key)
  
  # Set up the basic date filters
  base_filters <- list(
    c("award_notice_date", "DATE_GTE", format(start_date, "%Y-%m-%d")),
    c("award_notice_date", "DATE_LTE", format(today, "%Y-%m-%d"))
  )
  
  # Process additional filters from command line arguments
  additional_filters <- list()
  if (!is.null(opt$filter)) {
    # Split multiple filters by semicolon
    filter_strings <- unlist(strsplit(opt$filter, ";"))
    
    for (filter_string in filter_strings) {
      # Split each filter into column,operator,value
      filter_parts <- unlist(strsplit(filter_string, ","))
      if (length(filter_parts) == 3) {
        additional_filters <- c(additional_filters, list(filter_parts))
      } else {
        warning(paste("Ignoring invalid filter:", filter_string))
      }
    }
  }
  
  # Combine all filters
  all_filters <- c(base_filters, additional_filters)
  
  # Apply the filters to the query
  data <- labkey.selectRows(
      baseUrl    = "https://dholk.primate.wisc.edu",
      folderPath = "/dho/public/reporter",
      schemaName = "lists",
      queryName  = "nih_reporter",
      colSelect  = c("award_notice_date", "award_amount", "org_state", "administering_ic", "project_terms"),
      colFilter  = do.call(makeFilter, all_filters)
  )
  
  # Clean up column names
  names(data) <- tolower(gsub(" ", "_", names(data)))
  
  ## ----- Data Processing ---------
  # Convert amounts to numeric
  data$award_amount <- as.numeric(data$award_amount)
  
  # Create base data with both calendar and fiscal year information
  processed_data <- data %>%
    filter(!is.na(award_notice_date)) %>%
    mutate(
      cal_year = year(award_notice_date),
      cal_doy = yday(award_notice_date),
      fy = ifelse(month(award_notice_date) >= 10, year(award_notice_date) + 1, year(award_notice_date)),
      fy_day = as.numeric(difftime(
        award_notice_date, 
        as.Date(paste0(year(award_notice_date) - ifelse(month(award_notice_date) >= 10, 0, 1), "-10-01")), 
        units = "days"
      )) + 1,
      award_amount = ifelse(is.na(award_amount), 0, award_amount)
    ) %>%
    # Join with inflation factors
    left_join(cal_inflation_factors, by = c("cal_year" = "year")) %>%
    left_join(fy_inflation_factors, by = "fy") %>%
    # Apply inflation adjustments
    mutate(
      adjusted_amount_cal = award_amount * adj_factor.x,
      adjusted_amount_fy = award_amount * adj_factor.y
    )
  
  ## ----- Aggregations ---------
  # 1. Calendar year awards (counts)
  cal_daily_count <- processed_data %>%
    group_by(cal_year, cal_doy) %>%
    summarize(n = n(), .groups = "drop") %>%
    arrange(cal_year, cal_doy) %>%
    group_by(cal_year) %>%
    mutate(cum_n = cumsum(n)) %>%
    ungroup()
  
  # 2. Fiscal year awards (counts)
  fy_daily_count <- processed_data %>%
    group_by(fy, fy_day) %>%
    summarize(n = n(), .groups = "drop") %>%
    arrange(fy, fy_day) %>%
    group_by(fy) %>%
    mutate(cum_n = cumsum(n)) %>%
    ungroup()
  
  # 3. Calendar year award amounts
  cal_daily_amount <- processed_data %>%
    group_by(cal_year, cal_doy) %>%
    summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
    arrange(cal_year, cal_doy) %>%
    group_by(cal_year) %>%
    mutate(cum_amount = cumsum(amount)) %>%
    ungroup()
  
  # 4. Calendar year inflation-adjusted award amounts
  cal_daily_adjusted <- processed_data %>%
    group_by(cal_year, cal_doy) %>%
    summarize(amount = sum(adjusted_amount_cal, na.rm = TRUE), .groups = "drop") %>%
    arrange(cal_year, cal_doy) %>%
    group_by(cal_year) %>%
    mutate(cum_amount = cumsum(amount)) %>%
    ungroup()
  
  # 5. Fiscal year award amounts
  fy_daily_amount <- processed_data %>%
    group_by(fy, fy_day) %>%
    summarize(amount = sum(award_amount, na.rm = TRUE), .groups = "drop") %>%
    arrange(fy, fy_day) %>%
    group_by(fy) %>%
    mutate(cum_amount = cumsum(amount)) %>%
    ungroup()
  
  # 6. Fiscal year inflation-adjusted award amounts
  fy_daily_adjusted <- processed_data %>%
    group_by(fy, fy_day) %>%
    summarize(amount = sum(adjusted_amount_fy, na.rm = TRUE), .groups = "drop") %>%
    arrange(fy, fy_day) %>%
    group_by(fy) %>%
    mutate(cum_amount = cumsum(amount)) %>%
    ungroup()
  
  ## ----- Plot Creation Functions ---------
  # Helper function to create color palettes
  create_color_palette <- function(unique_years, current_year) {
    num_years <- length(unique_years)
    if (num_years == 0) return(c())
    
    year_positions <- match(unique_years, sort(unique_years))
    current_year_position <- match(current_year, sort(unique_years))
    
    pastel_colors <- scales::hue_pal(h.start = 120, l = 80)(num_years)
    
    if (current_year %in% unique_years) {
      pastel_colors[current_year_position] <- "#FF0000"
    }
    
    return(pastel_colors)
  }
  
  # 1. Calendar Year Count Plot
  create_cal_count_plot <- function(cal_daily_count_data) {
    unique_years <- unique(cal_daily_count_data$cal_year)
    color_values <- create_color_palette(unique_years, this_year)
    
    first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                               as.Date(paste0(this_year, "-12-01")), 
                               by = "month"))
    first_of_months <- first_of_months[first_of_months <= cal_cutoff]
    
    max_y_value <- cal_daily_count_data %>%
      filter(cal_doy <= cal_cutoff) %>%
      pull(cum_n) %>%
      max()
    
    ggplot(cal_daily_count_data,
           aes(x = cal_doy, y = cum_n,
               colour = factor(cal_year),
               size = cal_year == this_year)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, cal_cutoff),
                         breaks = first_of_months,
                         labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                                 as.Date(paste0(this_year, "-12-01")), 
                                                 by = "month")[1:length(first_of_months)],
                                        "%b %d")) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05)) +
      labs(title = "Cumulative NIH Awards (YTD) by Calendar Year",
           x = "Date",
           y = "Cumulative Award Count") +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1),
            plot.title = element_text(size = 12))
  }
  
  # 2. Fiscal Year Count Plot
  create_fy_count_plot <- function(fy_daily_count_data) {
    unique_years <- unique(fy_daily_count_data$fy)
    color_values <- create_color_palette(unique_years, current_fy)
    
    month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
    month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
    month_names <- format(month_dates, "%b")
    month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
    month_labels <- month_names[1:length(month_breaks)]
    
    max_y_value <- fy_daily_count_data %>%
      filter(fy_day <= fy_cutoff) %>%
      pull(cum_n) %>%
      max()
    
    ggplot(fy_daily_count_data,
           aes(x = fy_day, y = cum_n,
               colour = factor(fy),
               size = fy == current_fy)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Fiscal Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, fy_cutoff),
                         breaks = month_breaks,
                         labels = month_labels) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05)) +
      labs(title = "Cumulative NIH Awards by Fiscal Year (October 1 - September 30)",
           x = "Month",
           y = "Cumulative Award Count") +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
            plot.title = element_text(size = 12))
  }
  
  # 3. Calendar Year Award Plot
  create_cal_amount_plot <- function(cal_daily_amount_data) {
    unique_years <- unique(cal_daily_amount_data$cal_year)
    color_values <- create_color_palette(unique_years, this_year)
    
    first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                             as.Date(paste0(this_year, "-12-01")), 
                             by = "month"))
    first_of_months <- first_of_months[first_of_months <= cal_cutoff]
    
    max_y_value <- cal_daily_amount_data %>%
      filter(cal_doy <= cal_cutoff) %>%
      pull(cum_amount) %>%
      max()
    
    ggplot(cal_daily_amount_data,
           aes(x = cal_doy, y = cum_amount,
               colour = factor(cal_year),
               size = cal_year == this_year)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, cal_cutoff),
                         breaks = first_of_months,
                         labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                                 as.Date(paste0(this_year, "-12-01")), 
                                                 by = "month")[1:length(first_of_months)],
                                        "%b %d")) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
      labs(title = "Cumulative NIH Award Amounts (YTD) by Calendar Year",
           x = "Date",
           y = "Cumulative Amount (Billions $)") +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1),
            plot.title = element_text(size = 12))
  }
  
  # 4. Calendar Year Inflation-Adjusted Amount Plot
  create_cal_adjusted_plot <- function(cal_daily_adjusted_data) {
    unique_years <- unique(cal_daily_adjusted_data$cal_year)
    color_values <- create_color_palette(unique_years, this_year)
    
    first_of_months <- yday(seq(as.Date(paste0(this_year, "-01-01")), 
                             as.Date(paste0(this_year, "-12-01")), 
                             by = "month"))
    first_of_months <- first_of_months[first_of_months <= cal_cutoff]
    
    max_y_value <- cal_daily_adjusted_data %>%
      filter(cal_doy <= cal_cutoff) %>%
      pull(cum_amount) %>%
      max()
    
    ggplot(cal_daily_adjusted_data,
           aes(x = cal_doy, y = cum_amount,
               colour = factor(cal_year),
               size = cal_year == this_year)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, cal_cutoff),
                       breaks = first_of_months,
                       labels = format(seq.Date(as.Date(paste0(this_year, "-01-01")), 
                                               as.Date(paste0(this_year, "-12-01")), 
                                               by = "month")[1:length(first_of_months)],
                                      "%b %d")) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05),
                       labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
      labs(title = "Inflation-Adjusted Cumulative NIH Award Amounts by Calendar Year",
           subtitle = paste0("All amounts in ", year(today), " dollars"),
           x = "Date",
           y = paste0("Cumulative Amount (Billions $, ", year(today), " dollars)")) +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 45, hjust = 1),
            plot.title = element_text(size = 12),
            plot.subtitle = element_text(size = 9))
  }
  
  # 5. Fiscal Year Amount Plot
  create_fy_amount_plot <- function(fy_daily_amount_data) {
    unique_years <- unique(fy_daily_amount_data$fy)
    color_values <- create_color_palette(unique_years, current_fy)
    
    month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
    month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
    month_names <- format(month_dates, "%b")
    month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
    month_labels <- month_names[1:length(month_breaks)]
    
    max_y_value <- fy_daily_amount_data %>%
      filter(fy_day <= fy_cutoff) %>%
      pull(cum_amount) %>%
      max()
    
    ggplot(fy_daily_amount_data,
           aes(x = fy_day, y = cum_amount,
               colour = factor(fy),
               size = fy == current_fy)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Fiscal Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, fy_cutoff),
                         breaks = month_breaks,
                         labels = month_labels) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05),
                         labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
      labs(title = "Cumulative NIH Award Amounts by Fiscal Year (October 1 - September 30)",
           x = "Month",
           y = "Cumulative Amount (Billions $)") +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
            plot.title = element_text(size = 12))
  }
  
  # 6. Fiscal Year Inflation-Adjusted Amount Plot
  create_fy_adjusted_plot <- function(fy_daily_adjusted_data) {
    unique_years <- unique(fy_daily_adjusted_data$fy)
    color_values <- create_color_palette(unique_years, current_fy)
    
    month_dates <- seq.Date(fiscal_start, fiscal_start + years(1) - days(1), by = "month")
    month_days_in_fy <- as.numeric(difftime(month_dates, fiscal_start, units = "days")) + 1
    month_names <- format(month_dates, "%b")
    month_breaks <- month_days_in_fy[month_days_in_fy <= fy_cutoff]
    month_labels <- month_names[1:length(month_breaks)]
    
    max_y_value <- fy_daily_adjusted_data %>%
      filter(fy_day <= fy_cutoff) %>%
      pull(cum_amount) %>%
      max()
    
    ggplot(fy_daily_adjusted_data,
           aes(x = fy_day, y = cum_amount,
               colour = factor(fy),
               size = fy == current_fy)) +
      geom_line() +
      scale_colour_manual(values = color_values, name = "Fiscal Year") +
      scale_size_manual(values = c(`TRUE` = 1.0, `FALSE` = 0.5), guide = "none") +
      scale_x_continuous(limits = c(1, fy_cutoff),
                         breaks = month_breaks,
                         labels = month_labels) +
      scale_y_continuous(limits = c(0, max_y_value * 1.05),
                         labels = scales::dollar_format(scale = 1/1e9, suffix = "B")) +
      labs(title = "Inflation-Adjusted Cumulative NIH Award Amounts by Fiscal Year",
           subtitle = paste0("All amounts in ", year(today), " dollars"),
           x = "Month",
           y = paste0("Cumulative Amount (Billions $, ", year(today), " dollars)")) +
      theme_bw(base_size = 11) +
      theme(axis.text.x = element_text(angle = 0, hjust = 0.5),
            plot.title = element_text(size = 12),
            plot.subtitle = element_text(size = 9))
  }
  
  ## ----- Create all plots ---------
  plot1 <- create_cal_count_plot(cal_daily_count)
  plot2 <- create_fy_count_plot(fy_daily_count)
  plot3 <- create_cal_amount_plot(cal_daily_amount)
  plot4 <- create_cal_adjusted_plot(cal_daily_adjusted)
  plot5 <- create_fy_amount_plot(fy_daily_amount)
  plot6 <- create_fy_adjusted_plot(fy_daily_adjusted)
  
  ## ----- Set up the output file --------
  png(opt$output, width = 1600, height = 5400, res = 180)
  
  # Add an overall title and date stamp
  title_text <- paste0("NIH Awards Analysis Dashboard - Generated ", format(today, "%B %d, %Y"))
  # Add filter info to title if there are additional filters
  if (length(additional_filters) > 0) {
    filter_description <- paste(sapply(additional_filters, function(f) paste(f[1], f[2], f[3], sep=" ")), collapse=", ")
    title_text <- paste0(title_text, "\nFilters: ", filter_description)
  }
  
  title <- textGrob(
    title_text,
    gp = gpar(fontsize = 18, fontface = "bold")
  )
  
  # Arrange the plots vertically
  grid.arrange(
    title,
    plot1, plot2, plot3, plot4, plot5, plot6,
    ncol = 1,
    heights = c(0.4, 1, 1, 1, 1, 1, 1)
  )
  
  dev.off()
  cat(paste0("Dashboard created as ", opt$output, "\n"))
  
  # Generate additional dashboards
  generate_state_plots()
  generate_institute_plots()
  generate_condition_plots()
  
  # Upload the newly created files
  upload_files_to_webdav()
}

log_info('Session completed')
