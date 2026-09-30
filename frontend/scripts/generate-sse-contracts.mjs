import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

const frontendRoot = resolve(import.meta.dirname, '..')
const openapiPath = resolve(frontendRoot, '../backend/openapi.json')
const openapi = JSON.parse(readFileSync(openapiPath, 'utf8'))
const contracts = [
  {
    outputPath: resolve(frontendRoot, 'src/api/generated/task-events.ts'),
    path: '/api/v1/tasks/{task_id}/progress', method: 'get',
    expectedNames: ['TaskProgressEvent', 'TaskCompletedEvent', 'TaskErrorEvent', 'TaskCancelledEvent'],
  },
]

function resolveReference(reference) {
  const prefix = '#/components/schemas/'
  if (!reference.startsWith(prefix)) throw new Error(`Unsupported SSE schema reference: ${reference}`)
  const name = reference.slice(prefix.length)
  const schema = openapi.components?.schemas?.[name]
  if (!schema) throw new Error(`Missing SSE schema reference: ${reference}`)
  return schema
}

function zodFor(schema) {
  if (schema === true) return 'z.unknown()'
  if (schema === false) return 'z.never()'
  if (schema.$ref) return zodFor(resolveReference(schema.$ref))

  if (schema.oneOf) return `z.union([${schema.oneOf.map(zodFor).join(', ')}])`

  if (schema.anyOf) {
    const nullable = schema.anyOf.some((entry) => entry.type === 'null')
    const entries = schema.anyOf.filter((entry) => entry.type !== 'null')
    if (entries.length === 1) {
      const expression = zodFor(entries[0])
      return nullable ? `${expression}.nullable()` : expression
    }
    const expressions = schema.anyOf.map((entry) => entry.type === 'null' ? 'z.null()' : zodFor(entry))
    return `z.union([${expressions.join(', ')}])`
  }

  if (schema.type === 'array') return `z.array(${zodFor(schema.items)})`
  if (schema.type === 'object') {
    if (schema.additionalProperties && !schema.properties) {
      return `z.record(z.string(), ${zodFor(schema.additionalProperties)})`
    }
    if (schema.properties) {
      const required = new Set(schema.required ?? [])
      const properties = Object.entries(schema.properties).map(([name, property]) => {
        let expr = zodFor(property)
        if (property.default !== undefined) {
          expr += `.default(${JSON.stringify(property.default)})`
        } else if (!required.has(name)) {
          expr += '.optional()'
        }
        return `${JSON.stringify(name)}: ${expr}`
      })
      return `z.object({ ${properties.join(', ')} })`
    }
    return 'z.object({})'
  }
  if (schema.const !== undefined) return `z.literal(${JSON.stringify(schema.const)})`
  if (schema.enum && schema.type === 'string') return `z.enum([${schema.enum.map((value) => JSON.stringify(value)).join(', ')}])`
  let expression
  if (schema.type === 'string') expression = 'z.string()'
  else if (schema.type === 'integer') expression = 'z.number().int()'
  else if (schema.type === 'number') expression = 'z.number()'
  else if (schema.type === 'boolean') expression = 'z.boolean()'
  else if (schema.type === 'null') expression = 'z.null()'
  else throw new Error(`Unsupported SSE property schema: ${JSON.stringify(schema)}`)
  if (typeof schema.minimum === 'number') expression += `.min(${schema.minimum})`
  if (typeof schema.maximum === 'number') expression += `.max(${schema.maximum})`
  return expression
}

function emittedSchema(name) {
  const schema = openapi.components?.schemas?.[name]
  if (!schema || schema.type !== 'object' || !schema.properties) throw new Error(`Missing object schema ${name}`)
  const required = new Set(schema.required ?? [])
  const properties = Object.entries(schema.properties).map(([propertyName, property]) => {
    const optional = required.has(propertyName) ? '' : '.optional()'
    return `  ${JSON.stringify(propertyName)}: ${zodFor(property)}${optional},`
  })
  const constantName = `${name.charAt(0).toLowerCase()}${name.slice(1)}Schema`
  return `export const ${constantName} = z.object({\n${properties.join('\n')}\n})\n`
}

for (const contract of contracts) {
  const responseSchema = openapi.paths?.[contract.path]?.[contract.method]?.responses?.['200']?.content?.['text/event-stream']?.schema
  const actualNames = responseSchema?.oneOf?.map((entry) => entry.$ref?.split('/').at(-1))
  if (JSON.stringify(actualNames) !== JSON.stringify(contract.expectedNames)) {
    throw new Error(`OpenAPI SSE response ${contract.method.toUpperCase()} ${contract.path} has unexpected event schemas`)
  }
  const output = [
    '/* This file is generated from backend/openapi.json. Do not edit manually. */',
    "import { z } from 'zod'",
    '',
    ...contract.expectedNames.map(emittedSchema),
  ].join('\n')
  writeFileSync(contract.outputPath, `${output.trimEnd()}\n`, 'utf8')
}
