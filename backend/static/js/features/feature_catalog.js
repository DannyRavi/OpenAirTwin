import {deepMimoFeature} from "/js/features/deepmimo/index.js?v=20260723-empty-devices";
import {linkFeature} from "/js/features/link/index.js?v=20260723-empty-devices";
import {mobilityFeature} from "/js/features/mobility/index.js?v=20260723-empty-devices";
import {pmnetFeature} from "/js/features/pmnet/index.js?v=20261030-pmnet";
import {radarFeature} from "/js/features/radar/index.js?v=20260723-radar-shared-groups";
import {radiomapFeature} from "/js/features/radiomap/index.js?v=20260723-empty-devices";

export const FEATURE_CATALOG = Object.freeze([
  linkFeature,
  mobilityFeature,
  radiomapFeature,
  deepMimoFeature,
  radarFeature,
  pmnetFeature,
]);
