import {createPmnetJob, getPmnetJob, getPmnetResult} from "/js/api.js";

export function createPmnetTransport() {
  return {createPmnetJob, getPmnetJob, getPmnetResult};
}
