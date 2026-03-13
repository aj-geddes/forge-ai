import { create } from "zustand";
import type {
  AuthType,
  HTTPMethod,
  ParamType,
} from "@/types/config";

export interface Operation {
  operationId: string;
  method: string;
  path: string;
  summary: string;
  description?: string;
}

export interface ParameterDef {
  name: string;
  type: ParamType;
  description: string;
  required: boolean;
}

export interface FieldMapping {
  key: string;
  value: string;
}

export interface AuthData {
  type: AuthType;
  token: string;
  headerName: string;
  username: string;
  password: string;
}

export interface ResponseMappingData {
  resultPath: string;
  fieldMap: FieldMapping[];
}

export interface OpenApiData {
  specUrl: string;
  specTitle: string;
  specVersion: string;
  operations: Operation[];
  selected: string[];
  namespace: string;
  auth: AuthData;
}

export interface ManualData {
  name: string;
  description: string;
  baseUrl: string;
  endpoint: string;
  method: HTTPMethod;
  auth: AuthData;
  parameters: ParameterDef[];
  responseMapping: ResponseMappingData;
}

export interface WorkflowStepData {
  id: string;
  tool: string;
  params: string;
  outputAs: string;
  condition: string;
}

export interface WorkflowData {
  name: string;
  description: string;
  steps: WorkflowStepData[];
}

export type WizardType = "openapi" | "manual" | "workflow" | null;

const OPENAPI_MAX_STEP = 4;
const MANUAL_MAX_STEP = 6;

function defaultAuth(): AuthData {
  return {
    type: "none",
    token: "",
    headerName: "X-API-Key",
    username: "",
    password: "",
  };
}

function defaultOpenApiData(): OpenApiData {
  return {
    specUrl: "",
    specTitle: "",
    specVersion: "",
    operations: [],
    selected: [],
    namespace: "",
    auth: defaultAuth(),
  };
}

function defaultManualData(): ManualData {
  return {
    name: "",
    description: "",
    baseUrl: "",
    endpoint: "",
    method: "GET",
    auth: defaultAuth(),
    parameters: [],
    responseMapping: {
      resultPath: "",
      fieldMap: [],
    },
  };
}

function defaultWorkflowData(): WorkflowData {
  return {
    name: "",
    description: "",
    steps: [],
  };
}

function maxStepForWizard(type: WizardType): number {
  switch (type) {
    case "openapi":
      return OPENAPI_MAX_STEP;
    case "manual":
      return MANUAL_MAX_STEP;
    default:
      return 1;
  }
}

interface ToolWizardState {
  wizardType: WizardType;
  step: number;
  openApiData: OpenApiData;
  manualData: ManualData;
  workflowData: WorkflowData;

  openWizard: (type: WizardType) => void;
  closeWizard: () => void;
  nextStep: () => void;
  prevStep: () => void;
  setStep: (step: number) => void;

  setOpenApiData: (data: Partial<OpenApiData>) => void;
  setManualData: (data: Partial<ManualData>) => void;
  setWorkflowData: (data: Partial<WorkflowData>) => void;
  setOpenApiAuth: (data: Partial<AuthData>) => void;
  setManualAuth: (data: Partial<AuthData>) => void;
  setManualResponseMapping: (data: Partial<ResponseMappingData>) => void;

  addManualParameter: () => void;
  removeManualParameter: (index: number) => void;
  updateManualParameter: (index: number, data: Partial<ParameterDef>) => void;

  addFieldMapping: () => void;
  removeFieldMapping: (index: number) => void;
  updateFieldMapping: (index: number, data: Partial<FieldMapping>) => void;

  addWorkflowStep: () => void;
  removeWorkflowStep: (index: number) => void;
  updateWorkflowStep: (index: number, data: Partial<WorkflowStepData>) => void;
  moveWorkflowStep: (index: number, direction: "up" | "down") => void;

  toggleOperation: (operationId: string) => void;
  selectAllOperations: () => void;
  deselectAllOperations: () => void;
}

export const useToolStore = create<ToolWizardState>((set, get) => ({
  wizardType: null,
  step: 1,
  openApiData: defaultOpenApiData(),
  manualData: defaultManualData(),
  workflowData: defaultWorkflowData(),

  openWizard: (type) =>
    set({
      wizardType: type,
      step: 1,
      openApiData: defaultOpenApiData(),
      manualData: defaultManualData(),
      workflowData: defaultWorkflowData(),
    }),

  closeWizard: () =>
    set({
      wizardType: null,
      step: 1,
    }),

  nextStep: () => {
    const { step, wizardType } = get();
    const max = maxStepForWizard(wizardType);
    if (step < max) {
      set({ step: step + 1 });
    }
  },

  prevStep: () => {
    const { step } = get();
    if (step > 1) {
      set({ step: step - 1 });
    }
  },

  setStep: (step) => set({ step }),

  setOpenApiData: (data) =>
    set((state) => ({
      openApiData: { ...state.openApiData, ...data },
    })),

  setManualData: (data) =>
    set((state) => ({
      manualData: { ...state.manualData, ...data },
    })),

  setWorkflowData: (data) =>
    set((state) => ({
      workflowData: { ...state.workflowData, ...data },
    })),

  setOpenApiAuth: (data) =>
    set((state) => ({
      openApiData: {
        ...state.openApiData,
        auth: { ...state.openApiData.auth, ...data },
      },
    })),

  setManualAuth: (data) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        auth: { ...state.manualData.auth, ...data },
      },
    })),

  setManualResponseMapping: (data) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        responseMapping: { ...state.manualData.responseMapping, ...data },
      },
    })),

  addManualParameter: () =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        parameters: [
          ...state.manualData.parameters,
          { name: "", type: "string", description: "", required: false },
        ],
      },
    })),

  removeManualParameter: (index) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        parameters: state.manualData.parameters.filter((_, i) => i !== index),
      },
    })),

  updateManualParameter: (index, data) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        parameters: state.manualData.parameters.map((p, i) =>
          i === index ? { ...p, ...data } : p,
        ),
      },
    })),

  addFieldMapping: () =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        responseMapping: {
          ...state.manualData.responseMapping,
          fieldMap: [
            ...state.manualData.responseMapping.fieldMap,
            { key: "", value: "" },
          ],
        },
      },
    })),

  removeFieldMapping: (index) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        responseMapping: {
          ...state.manualData.responseMapping,
          fieldMap: state.manualData.responseMapping.fieldMap.filter(
            (_, i) => i !== index,
          ),
        },
      },
    })),

  updateFieldMapping: (index, data) =>
    set((state) => ({
      manualData: {
        ...state.manualData,
        responseMapping: {
          ...state.manualData.responseMapping,
          fieldMap: state.manualData.responseMapping.fieldMap.map((m, i) =>
            i === index ? { ...m, ...data } : m,
          ),
        },
      },
    })),

  addWorkflowStep: () =>
    set((state) => ({
      workflowData: {
        ...state.workflowData,
        steps: [
          ...state.workflowData.steps,
          {
            id: `step-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            tool: "",
            params: "{}",
            outputAs: "",
            condition: "",
          },
        ],
      },
    })),

  removeWorkflowStep: (index) =>
    set((state) => ({
      workflowData: {
        ...state.workflowData,
        steps: state.workflowData.steps.filter((_, i) => i !== index),
      },
    })),

  updateWorkflowStep: (index, data) =>
    set((state) => ({
      workflowData: {
        ...state.workflowData,
        steps: state.workflowData.steps.map((s, i) =>
          i === index ? { ...s, ...data } : s,
        ),
      },
    })),

  moveWorkflowStep: (index, direction) =>
    set((state) => {
      const steps = [...state.workflowData.steps];
      const targetIndex = direction === "up" ? index - 1 : index + 1;
      if (targetIndex < 0 || targetIndex >= steps.length) return state;

      const temp = steps[targetIndex]!;
      steps[targetIndex] = steps[index]!;
      steps[index] = temp;

      return {
        workflowData: {
          ...state.workflowData,
          steps,
        },
      };
    }),

  toggleOperation: (operationId) =>
    set((state) => {
      const selected = state.openApiData.selected.includes(operationId)
        ? state.openApiData.selected.filter((id) => id !== operationId)
        : [...state.openApiData.selected, operationId];
      return {
        openApiData: { ...state.openApiData, selected },
      };
    }),

  selectAllOperations: () =>
    set((state) => ({
      openApiData: {
        ...state.openApiData,
        selected: state.openApiData.operations.map((op) => op.operationId),
      },
    })),

  deselectAllOperations: () =>
    set((state) => ({
      openApiData: { ...state.openApiData, selected: [] },
    })),
}));
