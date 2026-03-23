import numpy as np
import GPyOpt
from GPyOpt.methods import BayesianOptimization
import yaml
from typing import Dict, List, Tuple, Callable
from loguru import logger


class ParameterSpace:
    
    def __init__(self, config_path: str = 'config/parameters.yml'):
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.parameters = self.config['parameters']
        self.param_names = list(self.parameters.keys())
    
    def get_gpyopt_space(self) -> List[Dict]:
        space = []
        
        for name, param in self.parameters.items():
            if param['type'] == 'continuous':
                space.append({
                    'name': name,
                    'type': 'continuous',
                    'domain': (param['min'], param['max'])
                })
            elif param['type'] == 'discrete':
                space.append({
                    'name': name,
                    'type': 'discrete',
                    'domain': tuple(range(param['min'], param['max'] + 1))
                })
        
        return space
    
    def normalize_config(self, config: Dict) -> np.ndarray:
        normalized = []
        
        for name in self.param_names:
            param = self.parameters[name]
            value = config[name]
            
            if param.get('log_scale', False):
                # Логарифмическая нормализация
                log_min = np.log(param['min'])
                log_max = np.log(param['max'])
                log_value = np.log(value)
                norm_value = (log_value - log_min) / (log_max - log_min)
            else:
                # Линейная нормализация
                norm_value = (value - param['min']) / (param['max'] - param['min'])
            
            normalized.append(norm_value)
        
        return np.array(normalized)
    
    def denormalize_config(self, normalized: np.ndarray) -> Dict:
        config = {}
        
        for i, name in enumerate(self.param_names):
            param = self.parameters[name]
            norm_value = normalized[i]
            
            if param.get('log_scale', False):
                # Обратное логарифмическое преобразование
                log_min = np.log(param['min'])
                log_max = np.log(param['max'])
                log_value = norm_value * (log_max - log_min) + log_min
                value = np.exp(log_value)
            else:
                # Обратное линейное преобразование
                value = norm_value * (param['max'] - param['min']) + param['min']
            
            # Округление для дискретных параметров
            if param['type'] == 'discrete':
                value = int(round(value))
            
            config[name] = value
        
        return config
    
    def get_default_config(self) -> Dict:
        return {name: param['default'] for name, param in self.parameters.items()}


class TimescaleDBOptimizer:
    
    def __init__(self, 
                 parameter_space: ParameterSpace,
                 objective_function: Callable,
                 initial_budget: int = 5,
                 total_budget: int = 30,
                 acquisition_type: str = 'EI'):
        
        self.parameter_space = parameter_space
        self.objective_function = objective_function
        self.initial_budget = initial_budget
        self.total_budget = total_budget
        self.acquisition_type = acquisition_type
        
        self.space = parameter_space.get_gpyopt_space()
        self.X = None  # Observations (configurations)
        self.Y = None  # Objective values
        
        logger.info(f"Initialized optimizer with {len(self.space)} parameters")
    
    def _objective_wrapper(self, x: np.ndarray) -> float:
        try:
            # Преобразование в конфигурацию
            config = {}
            for i, param_dict in enumerate(self.space):
                config[param_dict['name']] = x[0, i]
            
            logger.info(f"Evaluating configuration: {config}")
            
            # Вызов реальной целевой функции
            objective_value = self.objective_function(config)
            
            logger.info(f"Objective value: {objective_value}")
            
            # GPyOpt минимизирует, поэтому инвертируем для максимизации
            return -objective_value
            
        except Exception as e:
            logger.error(f"Error in objective function: {e}")
            return 1e10  # Большой штраф за ошибку
    
    def optimize(self) -> Dict:
        logger.info(f"Starting optimization with budget {self.total_budget}")
        
        # Создание BayesianOptimization объекта
        optimizer = BayesianOptimization(
            f=self._objective_wrapper,
            domain=self.space,
            model_type='GP',
            acquisition_type=self.acquisition_type,
            acquisition_jitter=0.01,
            exact_feval=False,
            maximize=False,  # Минимизируем (т.к. инвертировали)
            initial_design_numdata=self.initial_budget,
            initial_design_type='latin',
        )
        
        # Запуск оптимизации
        max_iter = self.total_budget - self.initial_budget
        optimizer.run_optimization(max_iter=max_iter, verbosity=True)
        
        # Получение лучшей конфигурации
        best_x = optimizer.x_opt
        best_config = {}
        for i, param_dict in enumerate(self.space):
            best_config[param_dict['name']] = best_x[i]
        
        best_objective = -optimizer.fx_opt  # Инвертируем обратно
        
        # Сохранение истории
        self.X = optimizer.X
        self.Y = -optimizer.Y  # Инвертируем обратно
        
        logger.info(f"Optimization completed. Best objective: {best_objective}")
        
        return {
            'best_configuration': best_config,
            'best_objective_value': best_objective,
            'all_configurations': self.X,
            'all_objectives': self.Y,
            'convergence_data': {
                'iterations': len(self.Y),
                'best_so_far': np.maximum.accumulate(self.Y.flatten()).tolist()
            }
        }
    
    def get_next_configuration(self) -> Dict:
        if self.X is None:
            # Первый запуск - возвращаем дефолтную конфигурацию
            return self.parameter_space.get_default_config()
        
        # TODO: Implement acquisition function для получения следующей точки
        pass


if __name__ == '__main__':
    # Тестовый запуск
    param_space = ParameterSpace()
    
    def dummy_objective(config):
        # Простая тестовая функция
        return config.get('shared_buffers', 0) / 1e9
    
    optimizer = TimescaleDBOptimizer(
        parameter_space=param_space,
        objective_function=dummy_objective,
        initial_budget=3,
        total_budget=10
    )
    
    # result = optimizer.optimize()
    # print(result)