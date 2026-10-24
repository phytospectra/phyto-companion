############################################################################
## NMR-based Metabolomics Pipeline using the metabom8 R package
##
## Workshop : NMR-based Metabolomics in Natural Products
## Hosts    : National Phenome Institute, Khon Kaen University
##            Chulabhorn Graduate Institute
##
## Study    : Curcuma longa (turmeric) rhizome extract - postharvest
##            curcuminoid degradation over storage time
##            5 time points (Day 0, 7, 14, 21, 28), n = 4 per time point
## Design   : CONTINUOUS / REGRESSION - storage_day is a numeric covariate,
##            NOT a category. Unlike the categorical designs used in the
##            earlier workshop datasets, this requires O-PLS REGRESSION
##            (continuous Y) rather than O-PLS-DA / PLS-DA (categorical Y).
## Target   : Curcuminoids (curcumin OCH3 ~3.85 ppm, enol-CH ~5.95 ppm,
##            aromatic H ~6.75-7.15 ppm) - decrease with storage time.
##            A generic oxidative degradation-product marker (~7.55 ppm)
##            increases over the same period.
##
## Pipeline : install -> import raw Bruker data -> preprocess -> PCA ->
##            O-PLS regression -> model validation -> trend/STOCSY analysis
##
## IMPORTANT: metabom8 imports REAL Bruker experiment folders (fid, acqus,
## pulseprogram, pdata/1/...). This script assumes such folders exist on
## disk, one per sample, as acquired on a TopSpin-controlled spectrometer.
############################################################################


## =========================================================================
## 0. INSTALL METABOM8 (Bioconductor)
## =========================================================================
if (!requireNamespace("BiocManager", quietly = TRUE)) {
  install.packages("BiocManager")
}
BiocManager::install("metabom8", update = FALSE, ask = FALSE)

needed <- c("ggplot2", "dplyr")
to_install <- needed[!needed %in% rownames(installed.packages())]
if (length(to_install)) install.packages(to_install)

# install.packages("remotes"); remotes::install_github("tkimhofer/metabom8")

library(metabom8)
library(ggplot2)
library(dplyr)


## =========================================================================
## 1. POINT TO RAW BRUKER EXPERIMENT FOLDERS
## =========================================================================
# Expected directory layout:
#   data_path/
#     CL_D00_S1/1/fid, acqus, pulseprogram, pdata/1/{procs,1r}
#     CL_D00_S2/1/... ... CL_D00_S4/1/...
#     CL_D07_S1/1/... ... CL_D28_S4/1/...
#
# Set this to the folder that CONTAINS all twenty sample subfolders:
data_path <- "path/to/Curcuma_longa_NMR_dataset"

# Path to the accompanying sample sheet (used in Section 3 to attach storage_day)
sample_sheet_path <- "path/to/sample_sheet_Curcuma_longa.csv"

exp_type <- list(pulprog = "noesygppr1d")


## =========================================================================
## 2. IMPORT SPECTRA
## =========================================================================
nmr_data <- read1d_proc(data_path, exp_type, n_max = 200, to_global = FALSE)

# Option B (raw FID, if TopSpin processing/pdata is unavailable):
# nmr_data <- read1d_raw(data_path, exp_type,
#   apodisation = list(fun = "exponential", lb = 0.3), zerofil = 1L, mode = "absorption")

X    <- nmr_data$X
ppm  <- nmr_data$ppm
meta <- nmr_data$meta

cat("Imported", nrow(X), "spectra with", ncol(X), "ppm variables\n")


## =========================================================================
## 3. ATTACH STORAGE DAY (CONTINUOUS COVARIATE) - STUDY DESIGN
## =========================================================================
imported_path <- rownames(X)
sample_id     <- basename(dirname(imported_path))   # -> "CL_D00_S1"

sheet <- read.csv(sample_sheet_path, stringsAsFactors = FALSE)
idx   <- match(sample_id, sheet$sample_id)

if (any(is.na(idx))) {
  stop("Some imported samples were not found in the sample sheet: ",
       paste(sample_id[is.na(idx)], collapse = ", "))
}

# NUMERIC, not a factor - this is a regression design
storage_day <- as.numeric(sheet$storage_day[idx])
run_order   <- sheet$run_order[idx]
batch_id    <- sheet$rhizome_batch_id[idx]

table(storage_day)   # sanity check: should read 4 at each of 0/7/14/21/28


## =========================================================================
## 4. VISUAL INSPECTION OF RAW SPECTRA
## =========================================================================
plot_spec(X, ppm, shift = c(0, 10))
plot_spec(X, ppm, shift = c(3.6, 7.8))   # zoom on curcuminoid/degradation region


## =========================================================================
## 5. PREPROCESSING PIPELINE
##    calibrate -> excise (cut TSP / cut water / cut noise) ->
##    baseline correction -> alignment -> PQN
## =========================================================================
excise_regions <- list(
  cut_noise_upfield   = c(min(ppm), -0.20),
  cut_TSP             = c(-0.20, 0.20),
  cut_water           = c(4.50, 5.20),
  cut_noise_downfield = c(9.70, max(ppm))
)

nmr_proc <- list(X = X, ppm = ppm, meta = meta) |>
  calibrate(type = "tsp") |>
  excise(regions = excise_regions) |>
  correct_baseline(method = "asls") |>
  align_spectra() |>
  pqn()

Xn   <- nmr_proc$X
ppmn <- nmr_proc$ppm

print_provenance(nmr_proc)

plot_spec(Xn, ppmn, shift = c(0, 10))
plot_spec(Xn, ppmn, shift = c(3.6, 7.8))


## =========================================================================
## 6. UNSUPERVISED ANALYSIS: PCA
## =========================================================================
uv <- uv_scaling(center = TRUE)

pca_model <- pca(Xn, scaling = uv, ncomp = 2)
show(pca_model)
summary(pca_model)

Tx <- as.data.frame(scores(pca_model))
colnames(Tx) <- paste0("PC", seq_len(ncol(Tx)))
Tx$StorageDay <- storage_day
Tx$ID         <- sample_id
Tx$RunOrder   <- run_order

r2 <- summary(pca_model)$perf$R2X

# Colour by the CONTINUOUS covariate - does PC1 track storage day?
ggplot(Tx, aes(PC1, PC2, colour = StorageDay, label = ID)) +
  geom_point(size = 3) +
  scale_colour_viridis_c(name = "Storage day") +
  theme_bw() +
  labs(title = "PCA scores \u2013 Curcuma longa, coloured by storage day",
       x = paste0("PC1 (", round(r2[1] * 100, 1), "%)"),
       y = paste0("PC2 (", round(r2[2] * 100, 1), "%)"))

# Batch-effect check
ggplot(Tx, aes(PC1, PC2, colour = RunOrder, label = ID)) +
  geom_point(size = 3) +
  scale_colour_viridis_c(name = "Run order") +
  theme_bw() +
  labs(title = "PCA scores coloured by acquisition run order (batch-effect check)",
       x = paste0("PC1 (", round(r2[1] * 100, 1), "%)"),
       y = paste0("PC2 (", round(r2[2] * 100, 1), "%)"))


## =========================================================================
## 7. SUPERVISED ANALYSIS: O-PLS REGRESSION (continuous storage_day)
## =========================================================================
# storage_day is NUMERIC here, so pls()/opls() should fit in REGRESSION
# mode rather than discriminant mode. Check ?opls / ?pls for how your
# installed version distinguishes regression vs. classification Y - some
# O-PLS implementations need an explicit argument (e.g. type = "regression"
# or similar); adjust the call below to match if it errors.
mc_cv_reg <- balanced_mc(k = 15, split = 2/3, type = "reg")

opls_reg_model <- opls(Xn, storage_day, scaling = uv, validation_strategy = mc_cv_reg)
show(opls_reg_model)
summary(opls_reg_model)

# Model validation
opls_perm(opls_reg_model, n = 100)
cv_anova(opls_reg_model)

dmodx(opls_reg_model, plot = TRUE)


## =========================================================================
## 8. O-PLS REGRESSION SCORES ("DEGRADATION AXIS")
## =========================================================================
Tp <- as.data.frame(scores(opls_reg_model))
To <- as.data.frame(scores(opls_reg_model, orth = TRUE))
colnames(Tp) <- "t_pred"
colnames(To) <- paste0("t_orth", seq_len(ncol(To)))

reg_df <- cbind(Tp, To, StorageDay = storage_day, ID = sample_id)

ggplot(reg_df, aes(t_pred, StorageDay)) +
  geom_point(size = 3, aes(colour = StorageDay)) +
  geom_smooth(method = "lm", se = TRUE, colour = "black", linetype = "dashed") +
  scale_colour_viridis_c() +
  theme_bw() +
  labs(title = "O-PLS predictive component vs. actual storage day",
       x = "Predictive component (t_pred)", y = "Storage day (actual)")


## =========================================================================
## 9. O-PLS LOADINGS & CANDIDATE DEGRADATION-RELATED REGIONS
## =========================================================================
Pp  <- as.numeric(loadings(opls_reg_model))
vip_scores <- as.numeric(vip(opls_reg_model))

loading_df <- data.frame(ppm = ppmn, loading = Pp, vip = vip_scores)

ggplot(loading_df, aes(ppm, loading, colour = vip)) +
  geom_line() +
  scale_x_reverse() +
  scale_colour_viridis_c(name = "VIP") +
  theme_bw() +
  labs(title = "O-PLS regression loadings coloured by VIP",
       x = "Chemical shift (ppm)", y = "Loading (covariance)")

candidates <- loading_df |>
  filter(abs(loading) > quantile(abs(loading), 0.99), vip > 1) |>
  arrange(desc(vip))

head(candidates, 20)


## =========================================================================
## 10. TREND ANALYSIS: CURCUMINOID DECAY vs. DEGRADATION MARKER RISE
## =========================================================================
get_marker <- function(ppm_lo, ppm_hi) {
  idx <- get_idx(ppmn, c(ppm_lo, ppm_hi))
  rowSums(Xn[, idx])
}

trend <- data.frame(
  ID              = sample_id,
  StorageDay      = storage_day,
  Curcumin_OCH3   = get_marker(3.80, 3.90),
  OxidationMarker = get_marker(7.50, 7.60)
)

# Simple correlation with storage day
cor.test(trend$Curcumin_OCH3, trend$StorageDay, method = "pearson")
cor.test(trend$OxidationMarker, trend$StorageDay, method = "pearson")

ggplot(trend, aes(StorageDay, Curcumin_OCH3)) +
  geom_point(size = 3, colour = "darkorange") +
  geom_smooth(method = "loess", se = TRUE, colour = "black") +
  theme_bw() +
  labs(title = "Curcumin OCH3 signal vs. storage day",
       x = "Storage day", y = "Integrated signal (a.u.)")

ggplot(trend, aes(StorageDay, OxidationMarker)) +
  geom_point(size = 3, colour = "firebrick") +
  geom_smooth(method = "loess", se = TRUE, colour = "black") +
  theme_bw() +
  labs(title = "Oxidation-product marker vs. storage day",
       x = "Storage day", y = "Integrated signal (a.u.)")

# STOCSY from the oxidation marker: does it anti-correlate with curcuminoids?
driver <- 7.55
stocsy_model <- stocsy(Xn, ppmn, driver)
plotStocsy(stocsy_model, shift = c(3.6, 7.8))


## =========================================================================
## 11. CANDIDATE METABOLITE SUMMARY
## =========================================================================
fit_lm <- lm(Curcumin_OCH3 ~ StorageDay, data = trend)
half_life_est <- log(2) / (-coef(fit_lm)[["StorageDay"]] / coef(fit_lm)[["(Intercept)"]])

cat("\n===================== CANDIDATE METABOLITES =====================\n")
cat("Curcumin (OCH3 ~3.85 ppm, enol-CH ~5.95 ppm, ArH ~6.75 ppm)\n")
cat("  Direction  : DECREASES with storage day\n")
cat(sprintf("  Pearson r  : %.2f\n", cor(trend$Curcumin_OCH3, trend$StorageDay)))
cat("Oxidation-product marker (~7.55 ppm)\n")
cat("  Direction  : INCREASES with storage day\n")
cat(sprintf("  Pearson r  : %.2f\n", cor(trend$OxidationMarker, trend$StorageDay)))
cat("Next step  : confirm curcuminoid identity with authentic standards\n")
cat("             and/or 2D NMR; consider a proper first-order decay fit\n")
cat("             (nls()) rather than the crude linear estimate above for\n")
cat("             a defensible shelf-life/half-life figure.\n")
cat("===================================================================\n")
